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

import re
from collections.abc import Sequence
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

import asyncpg

from app.db.session import authed_conn

# Type-only import: the meal literal is defined once (schemas/check_ins.py) so `meal` keeps
# its exact type through this layer instead of widening to `str | None` and forcing a cast.
from app.schemas.check_ins import Meal


class StoredFacts(NamedTuple):
    """The fact rows that actually landed, each with the id Postgres assigned.

    "Actually landed" is load-bearing: every insert here is `... where exists (parent is
    yours)` and RETURNING gives back nothing when that guard blocks the write. Recording
    only what RETURNING produced means the response can never claim a row we didn't write.
    """

    # (id, exercise_name, set_number, reps, weight_kg)
    sets: list[tuple[UUID, str, int, int, Decimal | None]]
    # (id, description, calories, protein_g, carbs_g, fat_g, meal)
    nutrition: list[tuple[UUID, str, Decimal, Decimal, Decimal, Decimal, Meal | None]]
    # (id, hours, quality)
    sleep: list[tuple[UUID, Decimal, int | None]]
    # (id, weight_kg)
    bodyweight: list[tuple[UUID, Decimal]]


# The four tables extraction owns, in the order a re-run clears them. Every one is
# check-in-scoped and user-owned.
_FACT_TABLES = ("workout_sets", "nutrition_entries", "sleep_entries", "bodyweight_entries")


def normalize_exercise_name(raw_name: str) -> str:
    """Fold an LLM's exercise name into the form the catalog is stored in.

    Lowercase, trimmed, internal whitespace collapsed — so "Bench Press", "bench press" and
    "  Bench   Press  " are one lookup, not three (AC row 7). Pure, so it's cheap to test and
    impossible to get subtly wrong against a live database.
    """
    return re.sub(r"\s+", " ", raw_name.strip().lower())


async def resolve_exercise(conn: asyncpg.Connection, raw_name: str) -> UUID | None:
    """The id of `raw_name` in the seeded catalog, or None if it isn't one (AC rows 7/13/24/26).

    A READ. Not a guarded write — a read.

    `exercises` is the ONE ownerless table: a shared catalog every user can select from, with
    no `user_id` to attribute a row to anyone. Its rows would otherwise be named by an LLM
    parsing a user's free text, so `backend.md` requires it contain **no user data**. The
    first design enforced that with a validating `security definer` function that inserted
    unknown names. It was wrong: it rejected AC row 13's *example* (an email — killed by
    banning `@` and `.`) but not the *category* the rule names. "the john smith special" is
    letters and spaces.

    So there is no write path at all now. The catalog is seeded by the migration, this
    function only looks names up, and `authenticated` holds `select` on `exercises` and
    nothing else. A name that isn't in the catalog returns None, the caller drops that set,
    and the check-in is marked 'partial'. Nothing a user can type reaches this table, because
    there is nowhere for it to go — which is a stronger guarantee than any regex, and it
    deleted a `security definer` privilege boundary (and its `search_path` trap) along with it.

    Takes a live `conn` rather than the pool: resolution happens inside the same transaction
    as the writes that use the returned id.
    """
    exercise_id: UUID | None = await conn.fetchval(
        "select id from public.exercises where name = $1",
        normalize_exercise_name(raw_name),
    )
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
    nutrition: Sequence[tuple[str, Decimal, Decimal, Decimal, Decimal, Meal | None]],
    sleep: Sequence[tuple[Decimal, int | None]],
    bodyweight: Sequence[Decimal],
) -> StoredFacts:
    """Replace this check-in's derived facts with the ones given, and return what landed.

    A `set` that comes back missing was rejected by the guard — that is what the caller
    turns into 'partial'.

    REPLACE, not append: extraction is re-runnable and the fact tables have no unique
    constraint to upsert against, so a second run that only inserted would silently double
    every set (AC row 8). Delete-then-insert, all in ONE transaction — a crash between the
    two must not leave the check-in with no facts, and a concurrent reader must never see
    the empty gap.

    `sets` carries the exercise's raw NAME, not an id: ids only exist on the far side of the
    guard, and the guard has to run inside this transaction.
    """
    stored = StoredFacts(sets=[], nutrition=[], sleep=[], bodyweight=[])
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
                # Not in the seeded catalog — either a name no user should reach the shared
                # table with (AC row 13) or a real lift we haven't seeded (AC row 26, the
                # accepted cost). Drop THIS set and keep going: one unrecognized name must
                # not cost the user the rest of their check-in.
                continue
            new_id: UUID | None = await conn.fetchval(
                "insert into public.workout_sets "
                "(user_id, check_in_id, exercise_id, set_number, reps, weight_kg) "
                "select $1, $2, $3, $4, $5, $6 "
                " where exists (select 1 from public.check_ins where id = $2 and user_id = $1) "
                "returning id",
                user_id,
                check_in_id,
                exercise_id,
                set_number,
                reps,
                weight_kg,
            )
            if new_id is not None:
                stored.sets.append((new_id, exercise_name, set_number, reps, weight_kg))

        for description, calories, protein_g, carbs_g, fat_g, meal in nutrition:
            new_id = await conn.fetchval(
                "insert into public.nutrition_entries "
                "(user_id, check_in_id, description, calories, protein_g, carbs_g, fat_g, meal) "
                "select $1, $2, $3, $4, $5, $6, $7, $8 "
                " where exists (select 1 from public.check_ins where id = $2 and user_id = $1) "
                "returning id",
                user_id,
                check_in_id,
                description,
                calories,
                protein_g,
                carbs_g,
                fat_g,
                meal,
            )
            if new_id is not None:
                stored.nutrition.append(
                    (new_id, description, calories, protein_g, carbs_g, fat_g, meal)
                )

        for hours, quality in sleep:
            new_id = await conn.fetchval(
                "insert into public.sleep_entries (user_id, check_in_id, hours, quality) "
                "select $1, $2, $3, $4 "
                " where exists (select 1 from public.check_ins where id = $2 and user_id = $1) "
                "returning id",
                user_id,
                check_in_id,
                hours,
                quality,
            )
            if new_id is not None:
                stored.sleep.append((new_id, hours, quality))

        for weight_kg_value in bodyweight:
            new_id = await conn.fetchval(
                "insert into public.bodyweight_entries (user_id, check_in_id, weight_kg) "
                "select $1, $2, $3 "
                " where exists (select 1 from public.check_ins where id = $2 and user_id = $1) "
                "returning id",
                user_id,
                check_in_id,
                weight_kg_value,
            )
            if new_id is not None:
                stored.bodyweight.append((new_id, weight_kg_value))

    return stored


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
                "select ws.id, ws.check_in_id, e.name as exercise_name, ws.set_number, ws.reps, "
                "       ws.weight_kg "
                "  from public.workout_sets ws "
                "  join public.exercises e on e.id = ws.exercise_id "
                " where ws.check_in_id = any($1) and ws.user_id = $2 "
                " order by e.name, ws.set_number",
                check_in_ids,
                user_id,
            ),
            "nutrition_entries": await conn.fetch(
                "select id, check_in_id, description, calories, protein_g, carbs_g, fat_g, meal "
                "  from public.nutrition_entries "
                " where check_in_id = any($1) and user_id = $2 "
                " order by created_at",
                check_in_ids,
                user_id,
            ),
            "sleep_entries": await conn.fetch(
                "select id, check_in_id, hours, quality from public.sleep_entries "
                " where check_in_id = any($1) and user_id = $2 "
                " order by created_at",
                check_in_ids,
                user_id,
            ),
            "bodyweight_entries": await conn.fetch(
                "select id, check_in_id, weight_kg from public.bodyweight_entries "
                " where check_in_id = any($1) and user_id = $2 "
                " order by created_at",
                check_in_ids,
                user_id,
            ),
        }
