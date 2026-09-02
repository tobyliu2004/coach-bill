"""Plan queries (issue #51). The ONLY layer that touches Postgres for this feature.

⚠️ EVERY STATEMENT IN THIS FILE CARRIES A CONTIGUOUS `<alias>.user_id = $1` ON BOTH SIDES OF
EVERY JOIN, AND ROW 12 ASSERTS IT BY READING THIS FILE'S SOURCE.

Why "both sides", when RLS is already a second lock and `test_data_isolation.py` already
greps for the word `user_id`: PR #45 measured it. Against local Postgres, with a
deliberately inconsistent row:

    fence only the fact side      -> 0 rows, safe
    fence only the PARENT side    -> 4995 kg of another user's volume under this user's date
    fence both (what ships)       -> 0 rows, safe

The leaking direction is the parent. And `test_data_isolation.py` cannot see it: the word
`user_id` already appears in a one-sided join, so its tripwire passes silently. That is why
row 12 reads the source for a CONTIGUOUS `<alias>.user_id = $1` per owned table instead.

Two mechanical rules follow from row 12 being a SOURCE SCAN, and breaking either makes the
assertion blind rather than red:
  * SQL is written as ADJACENT STRING LITERALS only — never a runtime `+`, never `.join()`,
    never an f-string of a fragment. The scanner reads literals out of the AST.
  * Every table is qualified `public.<table>`, because that is the pattern it matches on.

`resolve_exercise` is NOT re-implemented here — it lives in `db/facts.py` and is imported.
One catalog, one lookup, one alias-resolution rule.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

import asyncpg

from app.db.facts import resolve_exercise
from app.db.session import authed_conn


class MaterializedItemRow:
    """One planned set, ready to write: the template's numbers plus a RESOLVED exercise id.

    A plain value object rather than a Pydantic model — it never crosses the wire, and
    `db/` is the layer that owns it.
    """

    __slots__ = ("exercise_id", "position", "reps", "set_number", "weight_kg")

    def __init__(
        self,
        *,
        exercise_id: UUID,
        position: int,
        set_number: int,
        reps: int,
        weight_kg: Decimal | None,
    ) -> None:
        self.exercise_id = exercise_id
        self.position = position
        self.set_number = set_number
        self.reps = reps
        self.weight_kg = weight_kg


class MaterializedDayRow:
    """One dated day, ready to write."""

    __slots__ = ("day_date", "focus", "items", "week_number")

    def __init__(
        self,
        *,
        day_date: date,
        week_number: int,
        focus: str,
        items: list[MaterializedItemRow],
    ) -> None:
        self.day_date = day_date
        self.week_number = week_number
        self.focus = focus
        self.items = items


async def get_active_plan_row(pool: asyncpg.Pool, user_id: UUID) -> asyncpg.Record | None:
    """The caller's active plan header, or None.

    One owned table, so the owner filter may be written unqualified — there is nothing for
    it to be ambiguous about. It is still the first lock, and RLS is still the second.
    """
    async with authed_conn(pool, user_id) as conn:
        row: asyncpg.Record | None = await conn.fetchrow(
            "select p.id, p.status, p.starts_on, p.ends_on, p.weeks, p.goal_snapshot, "
            "       p.progression_note, p.calories_target, p.protein_g_target, "
            "       p.carbs_g_target, p.fat_g_target, p.created_at "
            "  from public.plans p "
            " where p.user_id = $1 and p.status = 'active'",
            user_id,
        )
        return row


async def get_plan_days(pool: asyncpg.Pool, user_id: UUID, plan_id: UUID) -> list[asyncpg.Record]:
    """Every dated day of one plan, with row 13's `logged` flag.

    ⚠️ FOUR OWNED TABLES, FOUR CONTIGUOUS FENCES — and the two inside the EXISTS are the
    ones row 13 is actually about. `logged` means "did THIS user train on this date", so the
    subquery joins `workout_sets` to `check_ins` for the date, and BOTH carry
    `<alias>.user_id = $1`. Fence only `ws` and another user's check-in supplies the date;
    fence only `ci` and another user's sets light up the flag. Row 13 asserts against a real
    database that B training on A's plan date leaves A's flag False.

    `logged` is COMPUTED, never stored. A stored flag goes stale the moment a check-in is
    edited or deleted, and would be a second opinion about what the user did — the same
    divergence `_trends_section` refuses to introduce.
    """
    async with authed_conn(pool, user_id) as conn:
        rows: list[asyncpg.Record] = await conn.fetch(
            "select pd.id, pd.day_date, pd.week_number, pd.focus, "
            "       exists ( "
            "         select 1 from public.workout_sets ws "
            "           join public.check_ins ci on ci.id = ws.check_in_id "
            "          where ws.user_id = $1 and ci.user_id = $1 "
            "            and ci.entry_date = pd.day_date "
            "       ) as logged "
            "  from public.plan_days pd "
            "  join public.plans p on p.id = pd.plan_id "
            " where pd.user_id = $1 and p.user_id = $1 and p.id = $2 "
            " order by pd.day_date",
            user_id,
            plan_id,
        )
        return rows


async def get_plan_items(pool: asyncpg.Pool, user_id: UUID, plan_id: UUID) -> list[asyncpg.Record]:
    """Every planned set of one plan, with its canonical catalog name.

    `public.exercises` is the ONE ownerless table (`backend.md`) — a shared catalog with no
    `user_id` to fence on — so it is deliberately absent from the owner filters. Both owned
    tables are fenced.
    """
    async with authed_conn(pool, user_id) as conn:
        rows: list[asyncpg.Record] = await conn.fetch(
            "select pi.id, pi.plan_day_id, pi.exercise_id, e.name as exercise_name, "
            "       pi.position, pi.set_number, pi.reps, pi.weight_kg "
            "  from public.plan_items pi "
            "  join public.plan_days pd on pd.id = pi.plan_day_id "
            "  join public.exercises e on e.id = pi.exercise_id "
            " where pi.user_id = $1 and pd.user_id = $1 and pd.plan_id = $2 "
            " order by pd.day_date, pi.position",
            user_id,
            plan_id,
        )
        return rows


async def resolve_item_exercises(
    pool: asyncpg.Pool, user_id: UUID, names: list[str]
) -> dict[str, UUID | None]:
    """Catalog ids for `names`, resolving aliases; None for a name the catalog lacks.

    ⚠️ TAKES THE POOL, NOT A LIVE `conn`, AND OPENS ITS OWN `authed_conn` — because THIS
    IS THE ONLY LAYER ALLOWED TO (`backend.md`: "db/ — the only layer that touches
    Postgres"). It used to take a `conn`, which meant `services/plans.py` had to import
    `app.db.session` and open the transaction itself — the only service in the codebase
    doing that (`profiles`, `check_ins`, `extraction`, `trends`, `coach` and `health` all
    call `db.<feature>` functions and nothing else). A new precedent, not an existing one.

    ⚠️ AND IT IS A SEPARATE TRANSACTION FROM THE WRITE. `insert_plan` opens its own. This
    docstring used to claim resolution happened "inside the SAME transaction as the ids'
    write", which was never true as wired — the kind of comment a reader trusts instead of
    checking.

    Harmless, and that is a property of `exercises` rather than of this code: it is the one
    ownerless table and has NO WRITE PATH AT ALL (#19), so a resolved id cannot be deleted
    or repointed between the two transactions. If the catalog ever gains one, this gap
    becomes real and resolution has to move inside `insert_plan`'s transaction — which is
    the other fix for the layering slip, and a bigger one, since `insert_plan` would then
    take names instead of resolved ids.

    Deduplicated: a week of "bench press" across four days is one lookup, not four. (#19's
    known cost — one lookup per set — is not repeated here.)
    """
    resolved: dict[str, UUID | None] = {}
    async with authed_conn(pool, user_id) as conn:
        for name in names:
            if name in resolved:
                continue
            resolved[name] = await resolve_exercise(conn, name)
    return resolved


async def insert_plan(
    pool: asyncpg.Pool,
    user_id: UUID,
    *,
    starts_on: date,
    ends_on: date,
    weeks: int,
    goal_snapshot: str | None,
    progression_note: str,
    calories_target: Decimal,
    protein_g_target: Decimal,
    carbs_g_target: Decimal,
    fat_g_target: Decimal,
    days: list[MaterializedDayRow],
) -> asyncpg.Record:
    """Archive the previous plan and write the new one — ALL OF IT, IN ONE TRANSACTION.

    ⚠️ THE TRANSACTION IS WHAT MAKES ROWS 2 AND 5 TRUE. "503 and nothing stored" needs no
    cleanup path here: the model call and its validation both happen BEFORE this function is
    entered, and everything inside it is one `authed_conn` transaction, so a failure on day
    19 of 28 leaves zero rows rather than a partial plan the user can see and cannot use.

    Row 14's archive-then-insert is correct for SEQUENTIAL requests only. Two concurrent
    callers both archive nothing and both insert; `plans_one_active_per_user` — a PARTIAL
    UNIQUE INDEX — is what actually guarantees one active plan, and the loser's
    `UniqueViolationError` propagates to the service, which converges on the winner (row 15).
    """
    async with authed_conn(pool, user_id) as conn:
        # Rule 3: `user_id` comes from the verified caller, never from a payload. `status`
        # is omitted so the column default ('active') supplies it — the app never names the
        # value on the way in, and the only status the app may write afterwards is through
        # the column-level `update (status)` grant.
        await conn.execute(
            "update public.plans set status = 'archived'  where user_id = $1 and status = 'active'",
            user_id,
        )

        plan = await conn.fetchrow(
            "insert into public.plans "
            "  (user_id, starts_on, ends_on, weeks, goal_snapshot, progression_note, "
            "   calories_target, protein_g_target, carbs_g_target, fat_g_target) "
            "values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) "
            "returning id, status, starts_on, ends_on, weeks, goal_snapshot, "
            "          progression_note, calories_target, protein_g_target, "
            "          carbs_g_target, fat_g_target, created_at",
            user_id,
            starts_on,
            ends_on,
            weeks,
            goal_snapshot,
            progression_note,
            calories_target,
            protein_g_target,
            carbs_g_target,
            fat_g_target,
        )
        if plan is None:  # pragma: no cover - an INSERT ... RETURNING always returns a row
            raise RuntimeError("inserting the plan returned no row")
        plan_id: UUID = plan["id"]

        # One statement for every day, not 28 round trips. `where exists` proves the parent
        # is the caller's INSIDE the write (backend rule 4) — `plan_id` was created two
        # statements ago and is not client input, but the guard is house style precisely so
        # nobody has to re-derive which parent ids are trustworthy.
        day_ids = await conn.fetch(
            "insert into public.plan_days (user_id, plan_id, day_date, week_number, focus) "
            "select $1, $2, d.day_date, d.week_number, d.focus "
            "  from unnest($3::date[], $4::smallint[], $5::text[]) "
            "         as d(day_date, week_number, focus) "
            " where exists ( "
            "         select 1 from public.plans p where p.id = $2 and p.user_id = $1 "
            "       ) "
            "returning id, day_date",
            user_id,
            plan_id,
            [d.day_date for d in days],
            [d.week_number for d in days],
            [d.focus for d in days],
        )

        id_of_date = {row["day_date"]: row["id"] for row in day_ids}
        item_day_ids: list[UUID] = []
        item_exercise_ids: list[UUID] = []
        item_positions: list[int] = []
        item_set_numbers: list[int] = []
        item_reps: list[int] = []
        item_weights: list[Decimal | None] = []
        for day in days:
            day_id = id_of_date[day.day_date]
            for item in day.items:
                item_day_ids.append(day_id)
                item_exercise_ids.append(item.exercise_id)
                item_positions.append(item.position)
                item_set_numbers.append(item.set_number)
                item_reps.append(item.reps)
                item_weights.append(item.weight_kg)

        # Row 4: a plan can legitimately have zero items (every one unresolvable), and an
        # empty `unnest` is a valid statement — but skipping it keeps the fake's router and
        # a real Postgres agreeing about what an empty write means.
        if item_day_ids:
            await conn.execute(
                "insert into public.plan_items "
                "  (user_id, plan_day_id, exercise_id, position, set_number, reps, weight_kg) "
                "select $1, i.plan_day_id, i.exercise_id, i.position, i.set_number, i.reps, "
                "       i.weight_kg "
                "  from unnest($2::uuid[], $3::uuid[], $4::smallint[], $5::smallint[], "
                "              $6::smallint[], $7::numeric[]) "
                "         as i(plan_day_id, exercise_id, position, set_number, reps, weight_kg) "
                # ⚠️ CORRELATED ON `pd.id = i.plan_day_id`, AND THE CORRELATION IS THE GUARD.
                # This read `where pd.plan_id = $8 and pd.user_id = $1`, which proves only
                # that SOME day of this plan is the caller's — the per-row parent id was
                # never mentioned, so the rule-4 guard was structurally blind while both the
                # docstring and row 12's scanner reported it as fenced (the scanner looks for
                # a contiguous `pd.user_id = $1`, which was present). Not exploitable today,
                # because these ids come from the `returning` of the insert two statements
                # up; the point is that the net was decorative, so the day someone appends
                # items to a client-named `plan_day_id` it would have kept saying "covered".
                " where exists ( "
                "         select 1 from public.plan_days pd "
                "          where pd.id = i.plan_day_id and pd.plan_id = $8 "
                "            and pd.user_id = $1 "
                "       )",
                user_id,
                item_day_ids,
                item_exercise_ids,
                item_positions,
                item_set_numbers,
                item_reps,
                item_weights,
                plan_id,
            )

        return plan
