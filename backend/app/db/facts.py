"""Derived-fact queries — the four tables extraction writes.

Every statement filters on the verified user id in the SAME statement (the first lock) and
runs inside `authed_conn`, so the owner-only RLS policies engage as a second lock (see
app/db/session.py and .claude/rules/backend.md).

⚠️ READ THIS BEFORE TOUCHING THE INSERTS. Unlike everywhere else in this codebase, **RLS
does not back up the ownership check here.** A fact row that user B tries to hang off user
A's check-in would carry `user_id = B`, which satisfies `auth.uid() = user_id` — the policy
is perfectly happy to let it sit on A's check-in. The `where exists (... and user_id = $1)`
guard (backend rule 4) is the ONLY lock. Do not remove it thinking RLS has your back.
Proven against a real database by `test_row14_user_b_cannot_attach_facts_to_user_as_check_in`.
"""

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

import asyncpg

from app.db.session import authed_conn

# The four tables extraction owns, in the order a re-run clears them. Every one is
# check-in-scoped and user-owned.
_FACT_TABLES = ("workout_sets", "nutrition_entries", "sleep_entries", "bodyweight_entries")


async def resolve_exercise(conn: asyncpg.Connection, raw_name: str) -> UUID | None:
    """The id for `raw_name` in the shared catalog, or None if the guard rejected it.

    `public.resolve_exercise` is a `security definer` function and the ONLY write path into
    `exercises` — the app holds no insert privilege on that table (see the migration). It
    normalizes (lower/trim/collapse whitespace) so casings collapse to one row, and returns
    NULL for anything that isn't letters/spaces/hyphens, which is what keeps user data out
    of a catalog every user can read (AC rows 7, 13).

    Takes a live `conn` rather than the pool: exercise resolution happens inside the same
    transaction as the writes that use the returned id.
    """
    exercise_id: UUID | None = await conn.fetchval("select public.resolve_exercise($1)", raw_name)
    return exercise_id


async def replace_facts(
    pool: asyncpg.Pool,
    user_id: UUID,
    check_in_id: UUID,
    *,
    # Sequence, not list: these are read-only inputs, and `list` is invariant — a caller's
    # list of a Literal meal type is not a `list[str | None]` as far as the type-checker is
    # concerned, even though every element is fine.
    sets: Sequence[tuple[str, int, int, Decimal | None]],
    nutrition: Sequence[tuple[str, Decimal, Decimal, Decimal, Decimal, str | None]],
    sleep: Sequence[tuple[Decimal, int | None]],
    bodyweight: Sequence[Decimal],
) -> list[tuple[str, int, int, Decimal | None]]:
    """Replace this check-in's derived facts with the ones given. Returns the `sets` that
    were actually stored — anything missing was rejected by the guard, which is what the
    caller turns into 'partial'.

    REPLACE, not append: extraction is re-runnable and the fact tables have no unique
    constraint to upsert against, so a second run that only inserted would silently double
    every set (AC row 8). Delete-then-insert, all in ONE transaction — a crash between the
    two must not leave the check-in with no facts, and a concurrent reader must never see
    the empty gap.

    `sets` carries the exercise's raw NAME, not an id: ids only exist on the far side of the
    guard, and the guard has to run inside this transaction.
    """
    stored_sets: list[tuple[str, int, int, Decimal | None]] = []
    async with authed_conn(pool, user_id) as conn:
        # Clear first. Owner-scoped in the same statement (backend rule 2) — `check_in_id`
        # came from a caller, so it is a claim, not a fact.
        for table in _FACT_TABLES:
            await conn.execute(
                f"delete from public.{table} where check_in_id = $1 and user_id = $2",
                check_in_id,
                user_id,
            )

        for exercise_name, set_number, reps, weight_kg in sets:
            exercise_id = await resolve_exercise(conn, exercise_name)
            if exercise_id is None:
                # The guard rejected this name. Drop THIS set and keep going — one bad
                # name must not cost the user the rest of their check-in (AC row 13).
                continue
            await conn.execute(
                "insert into public.workout_sets "
                "(user_id, check_in_id, exercise_id, set_number, reps, weight_kg) "
                "select $1, $2, $3, $4, $5, $6 "
                " where exists (select 1 from public.check_ins where id = $2 and user_id = $1)",
                user_id,
                check_in_id,
                exercise_id,
                set_number,
                reps,
                weight_kg,
            )
            stored_sets.append((exercise_name, set_number, reps, weight_kg))

        for description, calories, protein_g, carbs_g, fat_g, meal in nutrition:
            await conn.execute(
                "insert into public.nutrition_entries "
                "(user_id, check_in_id, description, calories, protein_g, carbs_g, fat_g, meal) "
                "select $1, $2, $3, $4, $5, $6, $7, $8 "
                " where exists (select 1 from public.check_ins where id = $2 and user_id = $1)",
                user_id,
                check_in_id,
                description,
                calories,
                protein_g,
                carbs_g,
                fat_g,
                meal,
            )

        for hours, quality in sleep:
            await conn.execute(
                "insert into public.sleep_entries (user_id, check_in_id, hours, quality) "
                "select $1, $2, $3, $4 "
                " where exists (select 1 from public.check_ins where id = $2 and user_id = $1)",
                user_id,
                check_in_id,
                hours,
                quality,
            )

        for weight_kg_value in bodyweight:
            await conn.execute(
                "insert into public.bodyweight_entries (user_id, check_in_id, weight_kg) "
                "select $1, $2, $3 "
                " where exists (select 1 from public.check_ins where id = $2 and user_id = $1)",
                user_id,
                check_in_id,
                weight_kg_value,
            )

    return stored_sets


async def list_facts_for_check_ins(
    pool: asyncpg.Pool, user_id: UUID, check_in_ids: list[UUID]
) -> dict[str, list[asyncpg.Record]]:
    """Every fact row the caller owns on the given check-ins, keyed by table.

    One query per table over ALL the ids (`= any($1)`) rather than per check-in: the list
    endpoint bundles facts, so per-row queries would be an N+1 that grows with a user's day.
    Scoped by `user_id` as well as the ids — the ids came from a prior read, but a read is
    not a permission, and this is a brand-new leak surface (AC row 15).
    """
    if not check_in_ids:
        return {table: [] for table in _FACT_TABLES}

    async with authed_conn(pool, user_id) as conn:
        return {
            # exercises is the shared, ownerless catalog — joined for the display name only.
            # The owner filter lives on workout_sets, which is the user-owned side.
            "workout_sets": await conn.fetch(
                "select ws.check_in_id, e.name as exercise_name, ws.set_number, ws.reps, "
                "       ws.weight_kg "
                "  from public.workout_sets ws "
                "  join public.exercises e on e.id = ws.exercise_id "
                " where ws.check_in_id = any($1) and ws.user_id = $2 "
                " order by e.name, ws.set_number",
                check_in_ids,
                user_id,
            ),
            "nutrition_entries": await conn.fetch(
                "select check_in_id, description, calories, protein_g, carbs_g, fat_g, meal "
                "  from public.nutrition_entries "
                " where check_in_id = any($1) and user_id = $2 "
                " order by created_at",
                check_in_ids,
                user_id,
            ),
            "sleep_entries": await conn.fetch(
                "select check_in_id, hours, quality from public.sleep_entries "
                " where check_in_id = any($1) and user_id = $2 "
                " order by created_at",
                check_in_ids,
                user_id,
            ),
            "bodyweight_entries": await conn.fetch(
                "select check_in_id, weight_kg from public.bodyweight_entries "
                " where check_in_id = any($1) and user_id = $2 "
                " order by created_at",
                check_in_ids,
                user_id,
            ),
        }
