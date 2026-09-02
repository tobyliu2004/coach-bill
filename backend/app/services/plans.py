"""Plan business logic (issue #51).

Two halves, and the split is the whole design:

  * `materialize` is PURE. No clock, no pool, no DB. It turns a 7-day template plus a start
    date into dated days, which is what makes rows 6 and 7 questions about a VALUE rather
    than about an environment — the same doctrine as `build_context` and `app/time.py`.
  * `create_plan` owns the effects, in a fixed order, and FAILS CLOSED.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

import asyncpg

from app.ai.planner import Planner
from app.db import plans as plans_db
from app.db.plans import MaterializedDayRow, MaterializedItemRow
from app.db.profiles import get_user_goal, get_user_timezone
from app.schemas.plans import (
    DAYS_PER_WEEK,
    PlanDayOut,
    PlanItemOut,
    PlanOut,
    PlanTemplate,
)
from app.services.coach import context_for
from app.time import local_today

logger = logging.getLogger(__name__)


class PlannerUnavailable(Exception):
    """The plan could not be generated. The route turns this into a 503.

    ⚠️ THIS PATH FAILS CLOSED — THE OPPOSITE OF EXTRACTION, AND ON PURPOSE.
    `POST /check-ins` swallows AI failures because losing facts must never cost a user their
    words: the text is already committed, and a dead vendor costs you facts, never the 201.
    Here there is nothing to preserve. A half-written or missing plan stored as if it were
    real is worse than no plan, so nothing is written at all and the request is retryable —
    the same call `POST /check-ins/{id}/reply` made (#21 design decision 2).
    """


class MaterializedDay:
    """One dated day of the expanded plan, before anything touches the database.

    Carries the template's items verbatim: `materialize` does not resolve exercise names,
    because resolution is a DB read and this function is pure.
    """

    __slots__ = ("day_date", "focus", "items", "week_number")

    def __init__(
        self,
        *,
        day_date: date,
        week_number: int,
        focus: str,
        items: list[tuple[str, int, int, Decimal | None]],
    ) -> None:
        self.day_date = day_date
        self.week_number = week_number
        self.focus = focus
        # (exercise name, set_number, reps, weight_kg) — the template's own numbers.
        self.items = items


def materialize(template: PlanTemplate, starts_on: date, weeks: int) -> list[MaterializedDay]:
    """Expand a 7-day template into `weeks * 7` dated days. PURE.

    Row 6's arithmetic, stated once: week *N* day *K* is `starts_on + 7(N-1) + (K-1)`, so
    the whole list is `starts_on + i` for i in `range(weeks * 7)` and each day inherits the
    template entry for its weekday. No gaps and no duplicates fall out of that by
    construction rather than by a check.

    Row 7 falls out too: the last date is `starts_on + weeks*7 - 1`, which is what satisfies
    `check (ends_on >= starts_on)` for every legal `weeks` — the smallest plan, one week, is
    still six days long.

    NO CLOCK, NO POOL, NO DB, and not a coroutine. That is asserted structurally by row 6's
    second test, because "pure" is only worth anything if it cannot quietly stop being true:
    a function that could reach for `datetime.now()` would make "does a 2019 start date
    produce a 2019 plan" a question about the environment.
    """
    days: list[MaterializedDay] = []
    for offset in range(weeks * DAYS_PER_WEEK):
        week_index, day_index = divmod(offset, DAYS_PER_WEEK)
        entry = template.days[day_index]
        days.append(
            MaterializedDay(
                day_date=starts_on + timedelta(days=offset),
                week_number=week_index + 1,
                focus=entry.focus,
                items=[
                    (item.exercise, item.set_number, item.reps, item.weight_kg)
                    for item in entry.items
                ],
            )
        )
    return days


def ends_on_for(starts_on: date, weeks: int) -> date:
    """Row 7's arithmetic, named once so the write and the response cannot disagree."""
    return starts_on + timedelta(days=weeks * DAYS_PER_WEEK - 1)


async def create_plan(pool: asyncpg.Pool, user_id: UUID, weeks: int, planner: Planner) -> PlanOut:
    """Generate a plan and store it. 503 and nothing written if anything goes wrong.

    THE ORDER IS THE DESIGN:

    1. Resolve `starts_on` from the CALLER'S local today (row 9), never the server's UTC
       date — the `local_today` doctrine. A plan generated at 23:30 in Los Angeles starts on
       that LA date, the same day their check-ins would land on.
    2. Snapshot the goal (row 10: NULL is legitimate) and assemble the context.
    3. Call the model, and let `PlanTemplate` validate what comes back. A `calories_target`
       of 900 raises `ValidationError` HERE (row 2).
    4. Only then open a transaction and write all three tables at once.

    Everything that can fail happens BEFORE the write, so "nothing stored" needs no cleanup
    path — there is nothing to clean up. That is the property rows 2 and 5 assert, and it is
    a consequence of the ordering rather than of a `try` block getting it right.
    """
    tz = await get_user_timezone(pool, user_id)
    starts_on = local_today(tz)
    goal_snapshot = await get_user_goal(pool, user_id)
    context = await context_for(pool, user_id, include_plan=False)

    try:
        template = await planner.plan(context=context, weeks=weeks)
    except Exception as exc:
        # Broad on purpose, and it does NOT swallow: a vendor error, a timeout and a
        # `ValidationError` from an out-of-range target are all "we cannot produce a plan",
        # and every one of them must reach the user as a retryable 503 rather than a 500.
        # Logged with the reason, because the alternative is a silent 503 nobody can debug.
        logger.warning("plan generation failed for user %s: %s", user_id, exc)
        raise PlannerUnavailable(str(exc)) from exc

    days = materialize(template, starts_on, weeks)
    rows = await _resolve_days(pool, user_id, days)

    try:
        plan = await plans_db.insert_plan(
            pool,
            user_id,
            starts_on=starts_on,
            ends_on=ends_on_for(starts_on, weeks),
            weeks=weeks,
            goal_snapshot=goal_snapshot,
            progression_note=template.progression_note,
            calories_target=template.calories_target,
            protein_g_target=template.protein_g_target,
            carbs_g_target=template.carbs_g_target,
            fat_g_target=template.fat_g_target,
            days=rows,
        )
    except asyncpg.UniqueViolationError as exc:
        # ⚠️ CONVERGE ON *THIS* CONSTRAINT ONLY, NEVER ON "a unique violation happened".
        # `plan_days` also carries `unique (plan_id, day_date)`. If that one ever fired, the
        # whole transaction would roll back — nothing stored, the archive undone — and the
        # convergence below would hand the caller `get_current_plan()`, i.e. their OLD plan,
        # under a 201. They asked for a new program and would get a success code and last
        # month's training, with an `info` log as the only trace. `materialize` cannot emit a
        # duplicate date today, so this is defensive; the reason it is worth two lines is
        # that a rolled-back transaction is indistinguishable from a successful one to the
        # caller unless you look at WHICH constraint blew up.
        if exc.constraint_name != "plans_one_active_per_user":
            raise
        # ROW 15 — CONVERGE, DO NOT CRASH. Two concurrent requests both found no active
        # plan and both tried to insert; `plans_one_active_per_user` let exactly one
        # through. The loser reads the winner's plan and returns it, so both callers see the
        # SAME plan and there is exactly one active row — the same move #21 made for
        # concurrent replies.
        #
        # It does NOT un-spend the loser's model call. That is honest rather than hidden: a
        # lock would pin a pooled connection for the whole generation and still not cover
        # two requests against two different users. Total spend is #26's job.
        logger.info("concurrent plan create for user %s; converging on the winner", user_id)
        winner = await get_current_plan(pool, user_id)
        if winner is None:  # pragma: no cover - the violation means a winner exists
            raise
        return winner

    return await _assemble(pool, user_id, plan)


async def get_current_plan(pool: asyncpg.Pool, user_id: UUID) -> PlanOut | None:
    """The caller's active plan, or None. The ROUTE decides None means 404 (`backend.md`)."""
    plan = await plans_db.get_active_plan_row(pool, user_id)
    if plan is None:
        return None
    return await _assemble(pool, user_id, plan)


async def _resolve_days(
    pool: asyncpg.Pool, user_id: UUID, days: list[MaterializedDay]
) -> list[MaterializedDayRow]:
    """Turn template exercise NAMES into catalog ids, dropping what the catalog lacks.

    ⚠️ ROWS 3 AND 4, AND THE DIFFERENCE BETWEEN THEM IS THE POINT.
    An unresolvable name drops that ITEM and keeps its day (row 3) — the same accepted cost
    `resolve_exercise` already imposes on a logged set (#19 AC row 26). A day whose EVERY
    item is unresolvable stores WITH ZERO ITEMS and keeps its focus (row 4): an empty
    training day is a gap the user can see and ask about, while a day relabelled "rest"
    would be the app telling them something false about their own program.
    """
    names = [name for day in days for name, _n, _r, _w in day.items]
    catalog = await plans_db.resolve_item_exercises(pool, user_id, names)

    rows: list[MaterializedDayRow] = []
    for day in days:
        items: list[MaterializedItemRow] = []
        for name, set_number, reps, weight_kg in day.items:
            exercise_id = catalog.get(name)
            if exercise_id is None:
                continue
            # `position` is assigned HERE, from the order the model wrote the day in, and it
            # is the index AFTER dropping unresolvable names so the stored positions are
            # contiguous. It cannot be recovered later: `set_number` restarts per exercise,
            # every row shares one `created_at`, and `id` is random — see the column's
            # comment in the migration. Row 3's dropped item shifts what follows it up,
            # which is right: the user's day is what was actually stored.
            items.append(
                MaterializedItemRow(
                    exercise_id=exercise_id,
                    position=len(items),
                    set_number=set_number,
                    reps=reps,
                    weight_kg=weight_kg,
                )
            )
        rows.append(
            MaterializedDayRow(
                day_date=day.day_date,
                week_number=day.week_number,
                focus=day.focus,
                items=items,
            )
        )
    return rows


async def _assemble(pool: asyncpg.Pool, user_id: UUID, plan: asyncpg.Record) -> PlanOut:
    """Header + days + items, read back and shaped for the wire.

    Read back rather than echoed from what we just built: the response then describes what
    the DATABASE holds, so a write that silently dropped rows shows up as a short plan on
    screen instead of a correct-looking payload over an empty table.
    """
    plan_id: UUID = plan["id"]
    day_rows = await plans_db.get_plan_days(pool, user_id, plan_id)
    item_rows = await plans_db.get_plan_items(pool, user_id, plan_id)

    items_by_day: dict[UUID, list[PlanItemOut]] = {}
    for row in item_rows:
        items_by_day.setdefault(row["plan_day_id"], []).append(
            PlanItemOut(
                id=row["id"],
                exercise_id=row["exercise_id"],
                exercise_name=row["exercise_name"],
                set_number=row["set_number"],
                reps=row["reps"],
                weight_kg=row["weight_kg"],
            )
        )

    days = [
        PlanDayOut(
            id=row["id"],
            day_date=row["day_date"],
            week_number=row["week_number"],
            focus=row["focus"],
            logged=bool(row["logged"]),
            items=items_by_day.get(row["id"], []),
        )
        for row in day_rows
    ]

    return PlanOut(
        id=plan_id,
        status=plan["status"],
        starts_on=plan["starts_on"],
        ends_on=plan["ends_on"],
        weeks=plan["weeks"],
        goal_snapshot=plan["goal_snapshot"],
        progression_note=plan["progression_note"],
        calories_target=plan["calories_target"],
        protein_g_target=plan["protein_g_target"],
        carbs_g_target=plan["carbs_g_target"],
        fat_g_target=plan["fat_g_target"],
        created_at=plan["created_at"],
        days=days,
    )
