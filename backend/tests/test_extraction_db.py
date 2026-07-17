"""Oracle suite for issue #19, section C — the real-database tier (rows 14-16).

Part of commit #1 on `feat/19-ai-extraction`, written BEFORE any implementation exists.
These encode section C of the 23-row correctness table Toby approved on 2026-07-16, plus
the real-DB half of rows 7, 8 and 13 (see "Why rows 7/8/13 also appear here" below).

Gated exactly like test_rls_identity.py, and for the same reason: `.env` is production and
connects as the BYPASSRLS `postgres` role, against which every assertion here would be a
false green.
  - RLS_DATABASE_URL       — the app pool as the fail-closed non-BYPASSRLS role
                             (locally `coach_app`); the code under test uses it.
  - RLS_ADMIN_DATABASE_URL — a privileged connection used ONLY by fixtures to seed
                             auth.users and to READ the fact tables past RLS when asserting
                             "user A's rows are untouched" (an assertion that must not be
                             made through the very policy it is testing).
Skipped unless RLS_DATABASE_URL is set; runs in #31's `rls-tests` job.

Why these three rows cannot be faked:
  - Row 16 is about GRANTs. The four fact tables have RLS + owner-only policies but ZERO
    table grants today, so every insert as `authenticated` would fail "permission denied".
    Only a real connection as that role can see it. This test is load-bearing: it is red
    until the migration adds the grants.
  - Row 14 is about the rule-4 `where exists` guard executing. A fake cannot run SQL, so it
    cannot prove the guard binds. Note RLS does NOT save us here: B inserting a fact row
    with user_id = B satisfies `auth.uid() = user_id`, so the ONLY thing stopping B from
    hanging facts off A's check-in is the parent-ownership guard inside the write.
  - Row 15 is about what the bundling actually returns from a database holding two users'
    data.

Why rows 7/8/13 also appear here (a deliberate addition — flagged to Toby):
the table files rows 7, 8 and 13 under section A ("fake extractor, runs in CI"), and the
fake-tier halves live in test_extraction.py. But their load-bearing claims are SQL
behaviour: "resolves to the seeded row" (row 7) and "does not resolve" (row 13) are what
the catalog lookup does, and "not duplicated" (row 8) is a row count. A fake pool asserting
those would only be asserting about the fake. These add the real proof; they do not replace
or weaken the section-A tests.

AMENDED 2026-07-16 (Toby, on PR #36): rows 7 and 13 are rewritten and rows 24-26 added.
`exercises` becomes a SEEDED canonical catalog and the app loses its write path entirely —
so the question a name asks is no longer "is your shape allowed?" but "are you in the
catalog?". Consequences encoded below:
  - Row 7 resolves AGAINST a seeded row and mints nothing.
  - Row 13's old `"x"*64 is not None` / `"pull-up" is not None` assertions are now WRONG and
    are inverted/re-justified in place; `"the john smith special"` — the case the shipped
    charset guard let through, and the reason the table moved — is now covered.
  - Rows 24-26 are new.

AMENDED again 2026-07-16 (Toby, AMENDMENT #2 — aliases): `exercises` gains a self-referencing
`canonical_id uuid null references public.exercises(id)`. `canonical_id is null` means the row
IS a movement; set, it means the row is an ALIAS pointing at one. `resolve_exercise` returns
`coalesce(canonical_id, id)`, so `workout_sets.exercise_id` never points at an alias. Encoded:
  - Row 24's OPEN deviation is RESOLVED in the table's favour — `"  PULL  UP "` now resolves,
    via the alias `pull up` -> `pull-up`. Toby ruled the code moves; the flag is gone and the
    row's own example is restored.
  - Rows 28-29 are new.
The exact alias LIST is deliberately not pinned — only the behaviour the rows state, using the
examples the rows name. The seed is free to carry more.

These reach the catalog through `app.db.facts.resolve_exercise(conn, raw_name)`, the Python
seam every caller already uses — NOT through `select public.resolve_exercise($1)` the way
the pre-amendment tests did. Toby has not yet decided whether the lookup stays a (no longer
`security definer`) SQL function or becomes normalize-in-Python + a plain select; these
rows assert the BEHAVIOUR and so must not force that choice.
"""

import os
import uuid
from decimal import Decimal
from typing import Any

import asyncpg
import pytest

requires_rls_db = pytest.mark.skipif(
    not os.getenv("RLS_DATABASE_URL"),
    reason="RLS_DATABASE_URL not set; extraction real-DB suite skipped",
)

FACT_TABLES = ("workout_sets", "nutrition_entries", "sleep_entries", "bodyweight_entries")


def _require_admin_dsn() -> str:
    """The privileged fixture DSN, or fail loudly (never silently) if it's missing."""
    dsn = os.getenv("RLS_ADMIN_DATABASE_URL")
    if not dsn:
        pytest.fail(
            "RLS_DATABASE_URL is set but RLS_ADMIN_DATABASE_URL is not; the real-DB "
            "suite needs a privileged connection to seed auth.users and to read the fact "
            "tables past RLS when asserting another user's rows are untouched."
        )
    return dsn


async def _admin_seed_users(admin_dsn: str, *user_ids: uuid.UUID) -> None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        for uid in user_ids:
            await conn.execute(
                "insert into auth.users (id, email) values ($1, $2) on conflict do nothing",
                uid,
                f"{uid}@extraction.test",
            )
    finally:
        await conn.close()


async def _admin_delete_users(admin_dsn: str, *user_ids: uuid.UUID) -> None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute("delete from auth.users where id = any($1::uuid[])", list(user_ids))
    finally:
        await conn.close()


async def _admin_set_weight_unit(admin_dsn: str, user_id: uuid.UUID, unit: str) -> None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(
            "update public.profiles set weight_unit = $2 where id = $1", user_id, unit
        )
    finally:
        await conn.close()


async def _admin_count_facts(admin_dsn: str, table: str, check_in_id: uuid.UUID) -> int:
    """Count fact rows on a check-in from OUTSIDE RLS.

    Deliberately privileged: asserting "A's rows are untouched" through A's own policy
    would be asking the mechanism under test to grade itself.
    """
    assert table in FACT_TABLES  # never interpolate anything but this fixed list
    conn = await asyncpg.connect(admin_dsn)
    try:
        count: int = await conn.fetchval(
            f"select count(*) from public.{table} where check_in_id = $1", check_in_id
        )
        return count
    finally:
        await conn.close()


async def _admin_delete_exercises_named(admin_dsn: str, *names: str) -> None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute("delete from public.exercises where name = any($1::text[])", list(names))
    finally:
        await conn.close()


async def _admin_count_exercises(admin_dsn: str) -> int:
    """The TOTAL size of the shared catalog, read from outside RLS.

    Rows 7 and 25 both turn on "nothing was created". A count scoped to one name would
    miss a write that landed under a different (e.g. un-normalized) name, which is exactly
    the failure the seeded design exists to make impossible — so count everything.
    """
    conn = await asyncpg.connect(admin_dsn)
    try:
        count: int = await conn.fetchval("select count(*) from public.exercises")
        return count
    finally:
        await conn.close()


async def _require_seeded_catalog(admin_dsn: str) -> None:
    """The shared precondition of the amended rows 7/13/24/25/26: the catalog is SEEDED.

    Why this exists rather than each test just looking up the one name it needs: at oracle
    time `resolve_exercise` still MINTS rows, so an earlier test in the same session
    (rows 8/14/15 all extract "bench press") leaves that name behind — and a row-7 that only
    checked "is 'bench press' present?" went GREEN off that residue, passing for exactly the
    reason the amendment exists to delete. A test that passes because a sibling test wrote
    its precondition is a false green, not coverage.

    So the precondition is the CATALOG, not one name: several canonical movements the seed
    migration must carry, only one of which any single test extracts. No plausible minting
    accident produces all of them.
    """
    missing = [
        name
        for name in ("bench press", "pull-up", "squat", "deadlift")
        if await _admin_exercise_id(admin_dsn, name) is None
    ]
    assert not missing, (
        f"the seeded catalog is missing {missing} — `supabase/seed.sql` carries zero "
        "exercises today, so the ~150-movement seed migration is the precondition for the "
        "whole amended design. Until it lands, these rows are red for the RIGHT reason."
    )


async def _admin_exercise_id(admin_dsn: str, name: str) -> uuid.UUID | None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        ex_id: uuid.UUID | None = await conn.fetchval(
            "select id from public.exercises where name = $1", name
        )
        return ex_id
    finally:
        await conn.close()


async def _admin_exercise_row(admin_dsn: str, name: str) -> asyncpg.Record | None:
    """The raw catalog row (id + canonical_id) for a name, read from outside RLS.

    Row 28 turns on the DIFFERENCE between a row's own id and the id it resolves to, so it
    needs the row itself, not just `_admin_exercise_id`'s id. At oracle time this raises
    `UndefinedColumnError: column "canonical_id" does not exist` — the migration carrying
    amendment #2's self-reference is the precondition for the whole alias design, so that
    is the RIGHT red.
    """
    conn = await asyncpg.connect(admin_dsn)
    try:
        return await conn.fetchrow(
            "select id, canonical_id from public.exercises where name = $1", name
        )
    finally:
        await conn.close()


async def _admin_count_alias_rows(admin_dsn: str) -> int:
    """How many catalog rows are ALIASES (`canonical_id` set) rather than movements.

    Row 29's "aliases are seeded, never minted" is not covered by a total row count alone:
    see the test for why the two counts fail differently.
    """
    conn = await asyncpg.connect(admin_dsn)
    try:
        count: int = await conn.fetchval(
            "select count(*) from public.exercises where canonical_id is not null"
        )
        return count
    finally:
        await conn.close()


async def _admin_count_exercises_matching(admin_dsn: str, pattern: str) -> int:
    conn = await asyncpg.connect(admin_dsn)
    try:
        count: int = await conn.fetchval(
            "select count(*) from public.exercises where name ilike $1", pattern
        )
        return count
    finally:
        await conn.close()


async def _admin_check_in_status(admin_dsn: str, check_in_id: uuid.UUID) -> str:
    conn = await asyncpg.connect(admin_dsn)
    try:
        status: str = await conn.fetchval(
            "select extraction_status from public.check_ins where id = $1", check_in_id
        )
        return status
    finally:
        await conn.close()


async def _admin_set_exercise_ids(admin_dsn: str, check_in_id: uuid.UUID) -> list[uuid.UUID]:
    conn = await asyncpg.connect(admin_dsn)
    try:
        rows = await conn.fetch(
            "select exercise_id from public.workout_sets where check_in_id = $1 "
            "order by set_number",
            check_in_id,
        )
        return [r["exercise_id"] for r in rows]
    finally:
        await conn.close()


async def _seed_check_in(pool: asyncpg.Pool, user_id: uuid.UUID, text: str) -> uuid.UUID:
    """Write a check-in under that user's OWN identity (RLS with-check allows it)."""
    from app.db.session import authed_conn

    async with authed_conn(pool, user_id) as conn:
        cid: uuid.UUID = await conn.fetchval(
            "insert into public.check_ins (user_id, raw_text, source, entry_date) "
            "values ($1, $2, 'text', current_date) returning id",
            user_id,
            text,
        )
        return cid


async def _seed_exercise(admin_dsn: str, name: str) -> uuid.UUID:
    conn = await asyncpg.connect(admin_dsn)
    try:
        ex_id: uuid.UUID = await conn.fetchval(
            "insert into public.exercises (name) values ($1) "
            "on conflict (name) do update set name = excluded.name returning id",
            name,
        )
        return ex_id
    finally:
        await conn.close()


class _FakeExtractor:
    """Primed facts; the live model is section E's job, not this file's."""

    def __init__(self, facts: Any) -> None:
        self._facts = facts

    async def extract(self, text: str) -> Any:
        return self._facts


def _bench_facts(sets: int = 1) -> Any:
    from app.schemas.extraction import ExtractedFacts, ExtractedSet

    return ExtractedFacts(
        sets=[
            ExtractedSet(exercise_name="bench press", set_number=n, reps=8, weight=Decimal("135"))
            for n in range(1, sets + 1)
        ]
    )


def _sets_facts(exercise_name: str, *, sets: int = 1, weight: str = "135") -> Any:
    """Primed sets naming an arbitrary exercise — rows 24/25/26 turn on the NAME."""
    from app.schemas.extraction import ExtractedFacts, ExtractedSet

    return ExtractedFacts(
        sets=[
            ExtractedSet(exercise_name=exercise_name, set_number=n, reps=5, weight=Decimal(weight))
            for n in range(1, sets + 1)
        ]
    )


# =====================================================================================
# Row 16 — the grants
# =====================================================================================


# AC row 16: insert facts into each of the four fact tables as the non-BYPASSRLS app role
# -> SUCCEEDS. This is the whole test: the tables have RLS + owner-only policies but no
# `grant ... to authenticated`, so today every one of these fails with
# "permission denied for table". Postgres has TWO gates — the coarse table GRANT and the
# per-row policy — and a query needs both. RLS still fences the rows; the grant opens the
# door. One assertion per table, because a grant can be forgotten one table at a time.
@requires_rls_db
async def test_row16_app_role_can_insert_into_every_fact_table() -> None:
    from app.db.pool import close_pool, create_pool
    from app.db.session import authed_conn

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    ex_name = f"extraction-grant-test-{uuid.uuid4()}"
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        ex_id = await _seed_exercise(admin, ex_name)
        check_in_id = await _seed_check_in(pool, a, "grant probe")

        async with authed_conn(pool, a) as conn:
            await conn.execute(
                "insert into public.workout_sets "
                "(user_id, check_in_id, exercise_id, set_number, reps, weight_kg) "
                "values ($1, $2, $3, 1, 8, 61.235)",
                a,
                check_in_id,
                ex_id,
            )
            await conn.execute(
                "insert into public.nutrition_entries "
                "(user_id, check_in_id, description, calories, protein_g, carbs_g, fat_g) "
                "values ($1, $2, '4 eggs', 310, 25, 2, 22)",
                a,
                check_in_id,
            )
            await conn.execute(
                "insert into public.sleep_entries (user_id, check_in_id, hours) values ($1, $2, 6)",
                a,
                check_in_id,
            )
            await conn.execute(
                "insert into public.bodyweight_entries (user_id, check_in_id, weight_kg) "
                "values ($1, $2, 81.647)",
                a,
                check_in_id,
            )

        for table in FACT_TABLES:
            assert await _admin_count_facts(admin, table, check_in_id) == 1, (
                f"public.{table}: the app role could not insert — the grant is missing"
            )
    finally:
        await _admin_delete_users(admin, a)
        await _admin_delete_exercises_named(admin, ex_name)
        await close_pool(pool)


# AC row 16 (the other half of least privilege): the app role must also be able to DELETE
# its own fact rows — row 8's "replace" is a delete followed by an insert, so a
# select+insert-only grant would pass the test above and still break the re-run path.
@requires_rls_db
async def test_row16_app_role_can_delete_its_own_fact_rows() -> None:
    from app.db.pool import close_pool, create_pool
    from app.db.session import authed_conn

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        check_in_id = await _seed_check_in(pool, a, "delete probe")

        async with authed_conn(pool, a) as conn:
            await conn.execute(
                "insert into public.sleep_entries (user_id, check_in_id, hours) values ($1, $2, 6)",
                a,
                check_in_id,
            )
        assert await _admin_count_facts(admin, "sleep_entries", check_in_id) == 1

        async with authed_conn(pool, a) as conn:
            await conn.execute(
                "delete from public.sleep_entries where check_in_id = $1 and user_id = $2",
                check_in_id,
                a,
            )
        assert await _admin_count_facts(admin, "sleep_entries", check_in_id) == 0
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# =====================================================================================
# Row 14 — the mandatory cross-tenant test
# =====================================================================================


# AC row 14: user B + user A's check_in_id -> ZERO fact rows written onto A's check-in,
# and A's existing facts are UNCHANGED. Backend rule 4: a child row whose parent id came
# from the client proves the parent is the caller's INSIDE the write.
#
# RLS is not the guard here — B's rows would carry user_id = B and satisfy
# `auth.uid() = user_id` — so this test is the only thing standing between B and A's
# check-in. A's rows are counted with the admin connection, from outside the policy.
@requires_rls_db
async def test_row14_user_b_cannot_attach_facts_to_user_as_check_in() -> None:
    from app.db.pool import close_pool, create_pool
    from app.services.extraction import extract_and_store

    admin = _require_admin_dsn()
    a, b = uuid.uuid4(), uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a, b)
        await _admin_set_weight_unit(admin, a, "kg")
        await _admin_set_weight_unit(admin, b, "kg")

        a_check_in = await _seed_check_in(pool, a, "A: bench 135 4x8")
        await extract_and_store(
            pool, a, a_check_in, "bench 135 4x8", _FakeExtractor(_bench_facts(4))
        )
        assert await _admin_count_facts(admin, "workout_sets", a_check_in) == 4  # A's real facts

        # B aims extraction at A's check_in_id.
        await extract_and_store(
            pool, b, a_check_in, "B: bench 135 1x8", _FakeExtractor(_bench_facts(1))
        )

        # Nothing of B's landed on A's check-in, and A's four sets are untouched — not
        # deleted by B's "replace", not added to, not overwritten.
        assert await _admin_count_facts(admin, "workout_sets", a_check_in) == 4
        conn = await asyncpg.connect(admin)
        try:
            owners = await conn.fetch(
                "select distinct user_id from public.workout_sets where check_in_id = $1",
                a_check_in,
            )
        finally:
            await conn.close()
        assert [r["user_id"] for r in owners] == [a]  # every row on A's check-in is A's
        for table in FACT_TABLES:
            if table != "workout_sets":
                assert await _admin_count_facts(admin, table, a_check_in) == 0
    finally:
        await _admin_delete_users(admin, a, b)
        await close_pool(pool)


# =====================================================================================
# Row 15 — the bundled read surface
# =====================================================================================


# AC row 15: user B lists check-ins -> only B's check-ins, and only B's facts. Bundling
# facts into the list response is a NEW read surface, so it is a fresh leak opportunity:
# a join that forgets to scope the child rows leaks A's sets into B's response even while
# the parent list is correctly filtered.
@requires_rls_db
async def test_row15_list_returns_only_the_callers_check_ins_and_facts() -> None:
    from app.db.pool import close_pool, create_pool
    from app.services.check_ins import list_check_ins
    from app.services.extraction import extract_and_store

    admin = _require_admin_dsn()
    a, b = uuid.uuid4(), uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a, b)
        await _admin_set_weight_unit(admin, a, "kg")
        await _admin_set_weight_unit(admin, b, "kg")

        a_check_in = await _seed_check_in(pool, a, "A: bench 135 4x8")
        b_check_in = await _seed_check_in(pool, b, "B: bench 135 1x8")
        await extract_and_store(
            pool, a, a_check_in, "bench 135 4x8", _FakeExtractor(_bench_facts(4))
        )
        await extract_and_store(
            pool, b, b_check_in, "bench 135 1x8", _FakeExtractor(_bench_facts(1))
        )

        b_rows = await list_check_ins(pool, b)

        assert [r.id for r in b_rows] == [b_check_in]  # only B's check-ins
        assert len(b_rows[0].facts.sets) == 1  # B's ONE set — not A's four, not five
        assert b_rows[0].facts.sets[0].reps == 8
        assert b_rows[0].facts.sets[0].weight_kg == Decimal("135")

        a_rows = await list_check_ins(pool, a)
        assert [r.id for r in a_rows] == [a_check_in]
        assert len(a_rows[0].facts.sets) == 4  # A still sees exactly A's
    finally:
        await _admin_delete_users(admin, a, b)
        await close_pool(pool)


# =====================================================================================
# Real-DB halves of rows 7, 8 and 13 (the SQL claims a fake cannot make)
# =====================================================================================


# AC row 7 (AMENDED, real half): "Bench Press", "bench press" and "  Bench   Press  " all
# resolve to the SAME SEEDED row, and NOTHING is created — the row already existed.
#
# The intent is unchanged from the pre-amendment row (casing must not fragment the catalog);
# the mechanism is not. Normalization now resolves AGAINST a fixed catalog instead of
# deciding what to mint, so this asserts two separable things: the three spellings all land
# on the seeded id (normalization: lower, trim, collapse), and the catalog is exactly the
# size it was (no minting). An implementation that minted one row and pointed all three at
# it would satisfy the first assertion and fail the second — which is the whole amendment.
#
# Reached through `app.db.facts.resolve_exercise`, not `select public.resolve_exercise($1)`:
# the SQL function may disappear, the behaviour may not.
@requires_rls_db
async def test_row7_casings_and_spacing_resolve_to_the_same_seeded_row() -> None:
    from app.db.facts import resolve_exercise
    from app.db.pool import close_pool, create_pool
    from app.db.session import authed_conn

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        await _require_seeded_catalog(admin)

        seeded_id = await _admin_exercise_id(admin, "bench press")
        assert seeded_id is not None
        assert await _admin_count_exercises_matching(admin, "bench press") == 1  # not fragmented
        before = await _admin_count_exercises(admin)

        async with authed_conn(pool, a) as conn:
            first = await resolve_exercise(conn, "Bench Press")
            second = await resolve_exercise(conn, "bench press")
            spaced = await resolve_exercise(conn, "  Bench   Press  ")

        assert first == seeded_id  # cased
        assert second == seeded_id  # exact
        assert spaced == seeded_id  # trimmed + internal whitespace collapsed

        # Nothing was minted: the catalog is unchanged in SIZE, not merely un-fragmented
        # under this one name.
        assert await _admin_count_exercises(admin) == before
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# AC row 13 (AMENDED, real half): anything NOT in the seeded catalog resolves to None and
# writes nothing. There is no insert path, so no name a user's text can produce can ever
# reach `exercises` — the ownerless, shared, un-attributable table backend.md says must
# contain no user data.
#
# What changed and why it HAD to: the old row asserted `"x"*64 is not None` and
# `"pull-up" is not None` as proof "the guard is not simply reject-all". Those assertions
# tested a CHARSET, and `project-reviewer` found the hole on PR #36 — "the john smith
# special" is letters, spaces and hyphens, so it passed the shipped guard and landed in a
# catalog every user reads. The rule was never about shape. So:
#   - `"x"*64` must now resolve to None. It is not an exercise and never should have
#     resolved; the old assertion said its SHAPE was allowed, which is not a thing the
#     amended table permits anyone to conclude.
#   - `"pull-up"` resolves non-None ONLY BECAUSE IT IS SEEDED — see the separate assertion
#     below, which is deliberately written as "this is that seeded row's id", not "some id
#     came back". Nothing here says its shape is allowed.
@requires_rls_db
async def test_row13_names_outside_the_seeded_catalog_do_not_resolve() -> None:
    from app.db.facts import resolve_exercise
    from app.db.pool import close_pool, create_pool
    from app.db.session import authed_conn

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    leaky = "bench press — hmu at toby@gmail.com"
    pii = "the john smith special"  # the case the old charset guard let through
    unresolvable = [
        leaky,  # user data: an email address
        pii,  # user data the OLD guard accepted — letters, spaces, hyphens only
        "",  # empty
        "   ",  # whitespace only
        "x" * 64,  # AMENDED: not an exercise. Was asserted `is not None` — that was wrong.
        "x" * 65,  # ditto, and over the old length bound
        "squat 5x5",  # digits: a set description, not a movement
        "zercher squat",  # a REAL lift, deliberately unseeded (row 26's cost)
    ]
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        await _require_seeded_catalog(admin)
        before = await _admin_count_exercises(admin)

        async with authed_conn(pool, a) as conn:
            for name in unresolvable:
                assert await resolve_exercise(conn, name) is None, (
                    f"{name!r} is not in the seeded catalog and must not resolve"
                )

            # ...and the catalog is not simply "reject everything" (that would satisfy every
            # assertion above and be useless — see row 24). `pull-up` resolves for exactly
            # one reason: the migration seeded it. Asserting the SEEDED ID, not `is not
            # None`, is what stops this being read as "the shape is allowed".
            seeded_pull_up = await _admin_exercise_id(admin, "pull-up")
            assert seeded_pull_up is not None, "'pull-up' must be in the seed catalog"
            assert await resolve_exercise(conn, "pull-up") == seeded_pull_up

        # Nothing was created by any of the above — the catalog is byte-for-byte the size it
        # was, and no user data reached it under any spelling.
        assert await _admin_count_exercises(admin) == before
        assert await _admin_count_exercises_matching(admin, "%toby@gmail.com%") == 0
        assert await _admin_count_exercises_matching(admin, "%john smith%") == 0
        assert await _admin_count_exercises_matching(admin, "%zercher%") == 0
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# =====================================================================================
# Rows 24-26 — the seeded-catalog amendment (Toby, 2026-07-16, on PR #36)
# =====================================================================================


# AC row 24 (NEW): a SEEDED movement under any casing/spacing resolves to that seeded row's
# id, the set is stored, and status is 'done'.
#
# This row is the counterweight that keeps row 13 honest: a catalog that rejected everything
# would satisfy every assertion in row 13 and make the product useless. So this drives the
# FULL path — extract, resolve, store, decide status, STAMP it — over a name the catalog
# does admit.
#
# Driven through `create_check_in`, not `extract_and_store`: the status is DECIDED by
# `extract_and_store` (which returns it) and PERSISTED by `create_check_in`. A test that
# calls the decider and then asserts the stored column is asserting a claim that seam does
# not own — it read 'pending' because nothing had stamped it yet. The row's claim is that
# the check-in ENDS UP 'done', so the test drives the seam that owns the whole path and
# mints its own check-in, exactly as POST does. Asserting the persisted column (not just the
# returned value) is the stronger reading: a status the API reports but never writes would
# be a lie the next list request tells.
@requires_rls_db
async def test_row24_a_seeded_movement_resolves_stores_the_set_and_is_done() -> None:
    from app.db.facts import resolve_exercise
    from app.db.pool import close_pool, create_pool
    from app.db.session import authed_conn
    from app.schemas.check_ins import CheckInCreate
    from app.services.check_ins import create_check_in

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        await _admin_set_weight_unit(admin, a, "kg")
        await _require_seeded_catalog(admin)

        seeded_id = await _admin_exercise_id(admin, "pull-up")
        assert seeded_id is not None

        # Every casing/spacing the model might emit lands on the one seeded row.
        #
        # RESOLVED 2026-07-16 by AMENDMENT #2 — in the TABLE's favour. The oracle previously
        # flagged this as an OPEN deviation: row 24's own example is `"  PULL  UP "` (space,
        # no hyphen) against a catalog seeded as `pull-up`, which normalization (case +
        # whitespace only) cannot reach, so the test asserted `"  PULL-UP  "` and recorded
        # the divergence rather than hiding it. Toby ruled the CODE moves: `pull up` is now
        # a seeded ALIAS of `pull-up`, so the row's literal example is restored below.
        #
        # Note the mechanism is deliberately NOT pinned: the third assertion does not care
        # whether `"  PULL  UP "` arrives via an alias row or via normalization learning to
        # fold hyphens. It asserts the row's claim — that spelling lands on the canonical
        # seeded id — which is true under either.
        async with authed_conn(pool, a) as conn:
            assert await resolve_exercise(conn, "Pull-Up") == seeded_id
            assert await resolve_exercise(conn, "pull-up") == seeded_id
            assert await resolve_exercise(conn, "  PULL  UP ") == seeded_id

        created = await create_check_in(
            pool,
            a,
            CheckInCreate(text="pull-up 3x5"),
            _FakeExtractor(_sets_facts("Pull-Up", sets=3)),
        )
        check_in_id = created.id

        # The set is STORED, and pointed at the seeded row — not at a freshly minted one.
        assert await _admin_count_facts(admin, "workout_sets", check_in_id) == 3
        assert await _admin_set_exercise_ids(admin, check_in_id) == [seeded_id] * 3
        # Nothing was dropped, so this is a success, not a 'partial' — both as reported to
        # the caller and as actually written to the row.
        assert created.extraction_status == "done"
        assert await _admin_check_in_status(admin, check_in_id) == "done"
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# AC row 25 (NEW): the full extraction path runs as the `coach_app` role over text naming an
# UNSEEDED exercise -> the `exercises` row count is UNCHANGED. **Not "the guard declined" —
# the role holds NO INSERT PRIVILEGE on `exercises` and there is no `security definer`
# function to lend it one.**
#
# The row has two clauses and they are STRUCTURAL, so the test asserts them structurally and
# directly. An earlier version of this test only ran the extraction path and asserted the
# count was unchanged; that is a BEHAVIOURAL fact ("the app didn't try to write") and it is
# guaranteed by `extract_and_store` issuing a bare `select`. It passed identically whether or
# not the role held insert, and whether or not a definer function survived — i.e. it did not
# prove the claim the row makes. (Caught by `project-reviewer` on PR #36.)
#
# THE FAILURE THIS ROW IS DESIGNED AGAINST: a future ticket adds `grant insert on
# public.exercises to authenticated`, or restores a definer helper "to grow the catalog from
# unknowns" — a follow-up row 26's note explicitly anticipates. The app-behaviour assertion
# below would stay green through both, and the structural guarantee that justifies this
# entire redesign would be gone silently. Clauses 1 and 2 are what go red instead.
#
# Must run as `coach_app` (the RLS_DATABASE_URL pool — the role prod actually uses, via
# `authed_conn`'s `set local role authenticated`). It cannot be faked: a fake pool has no
# privileges to lack.
@requires_rls_db
async def test_row25_the_app_role_cannot_write_the_catalog_at_all() -> None:
    from app.db.pool import close_pool, create_pool
    from app.db.session import authed_conn
    from app.services.extraction import extract_and_store

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    unseeded = "zercher squat"
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        await _admin_set_weight_unit(admin, a, "kg")
        await _require_seeded_catalog(admin)

        assert await _admin_exercise_id(admin, unseeded) is None, (
            "'zercher squat' must NOT be seeded — row 26's accepted cost depends on it"
        )
        before = await _admin_count_exercises(admin)

        # ---- Clause 1: the role holds NO INSERT PRIVILEGE on `exercises`. ----
        # Asserted two ways, because either alone is escapable:
        #
        # (a) `has_table_privilege` reads the GRANT itself. This is the assertion that goes
        #     red the day someone writes `grant insert on public.exercises to authenticated`
        #     — and it is load-bearing precisely because (b) would NOT. `exercises` has RLS
        #     on with a select-only policy, so after such a grant an insert would still be
        #     refused — by the RLS gate — and would still raise the SAME 42501 error class.
        #     (b) cannot tell "no privilege" from "privilege, but no policy"; the row's claim
        #     is the former, so the row needs (a).
        # (b) The insert is actually attempted, as the role, against the real table. This is
        #     what proves the grant reading corresponds to reality — a privilege audited but
        #     never exercised is a claim about a catalog view, not about the database.
        async with authed_conn(pool, a) as conn:
            assert (
                await conn.fetchval(
                    "select has_table_privilege('authenticated', 'public.exercises', 'insert')"
                )
                is False
            ), (
                "`authenticated` holds INSERT on public.exercises — the app can write the "
                "shared catalog. Row 25's whole point is that this privilege does not exist: "
                "no write path, so no user text can ever reach an ownerless table that every "
                "user reads. If this grant is wanted, the table changes first, not the test."
            )

        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            async with authed_conn(pool, a) as conn:
                await conn.execute(
                    "insert into public.exercises (name) values ($1)",
                    "row25 direct privilege probe",
                )
        # The probe must not have landed even partially (the raise rolls its tx back).
        assert await _admin_count_exercises_matching(admin, "%row25%") == 0

        # ---- Clause 2: no `security definer` function can lend it one. ----
        # A definer function runs as its OWNER, so it bypasses both gates above: it would
        # hand the app back the write path clause 1 just removed, and clause 1 would still
        # be green. `public.resolve_exercise` was exactly such a function before this
        # amendment; the redesign deleted the privilege boundary rather than guarding it.
        # `public.handle_new_user` is a pre-existing, unrelated definer (it touches
        # `profiles`) and correctly does not match this predicate.
        async with authed_conn(pool, a) as conn:
            definers: list[str] = [
                r["proname"]
                for r in await conn.fetch(
                    "select proname from pg_proc where prosecdef and prosrc ilike '%exercises%'"
                )
            ]
        assert definers == [], (
            f"`security definer` function(s) {definers} reference `exercises` — one of them "
            "can insert into the catalog on the app's behalf regardless of the app role's "
            "grants, which re-opens the escalation surface this redesign exists to delete."
        )

        # ---- The end-to-end check: the real path over unseeded text writes nothing. ----
        # Worth keeping, but NOT sufficient on its own, and it is not what proves the row:
        # it only shows THIS implementation of `extract_and_store` doesn't attempt a write.
        # It would pass unchanged against a role holding insert and against a surviving
        # definer. Clauses 1 and 2 are what make those two futures fail; this asserts the
        # shipped path agrees with them today.
        check_in_id = await _seed_check_in(pool, a, "zercher squat 185 3x5")
        await extract_and_store(
            pool,
            a,
            check_in_id,
            "zercher squat 185 3x5",
            _FakeExtractor(_sets_facts(unseeded, sets=3, weight="185")),
        )

        assert await _admin_count_exercises(admin) == before
        assert await _admin_exercise_id(admin, unseeded) is None
        assert await _admin_count_exercises_matching(admin, "%zercher%") == 0
    finally:
        await _admin_delete_users(admin, a)
        # Sweep up any row a regressed implementation left behind, so a re-run fails on the
        # assertions above rather than on its own residue. With no write path these deletes
        # are no-ops — and if one ever stops being one, that IS the bug this row is about.
        await _admin_delete_exercises_named(admin, unseeded)
        await _admin_delete_exercises_named(admin, "row25 direct privilege probe")
        await close_pool(pool)


# AC row 26 (NEW): a real but UNSEEDED lift ("zercher squat 185 3x5") -> the set is dropped
# and the status is 'partial'.
#
# This row exists to make the accepted cost of the seeded design VISIBLE rather than a
# surprise: real lifts that aren't seeded don't log. It is the same drop path row 13 uses,
# reached by an honest input rather than an attack — which is why it is a separate row.
#
# The row's third clause — the UI says the exercise wasn't recognized, distinct from
# "extraction failed" and from "nothing found" — is a frontend claim and is covered by row
# 27 in frontend/src/lib/checkInView.test.ts. `partial` + zero surviving facts is the exact
# state row 27 consumes; this test pins the backend end of that contract.
#
# Driven through `create_check_in` for the same reason as row 24: `extract_and_store`
# decides the status and returns it, but `create_check_in` is what stamps it on the row.
@requires_rls_db
async def test_row26_an_unseeded_real_lift_is_dropped_and_marked_partial() -> None:
    from app.db.pool import close_pool, create_pool
    from app.schemas.check_ins import CheckInCreate
    from app.services.check_ins import create_check_in

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        await _admin_set_weight_unit(admin, a, "kg")

        created = await create_check_in(
            pool,
            a,
            CheckInCreate(text="zercher squat 185 3x5"),
            _FakeExtractor(_sets_facts("zercher squat", sets=3, weight="185")),
        )
        check_in_id = created.id

        # The set is DROPPED — not stored against a wrong exercise, not stored with a null
        # exercise_id.
        assert await _admin_count_facts(admin, "workout_sets", check_in_id) == 0
        # ...and this is 'partial': a fact was found and had to be thrown away. Not 'done'
        # (row 11/12's "nothing to extract" success) and not 'failed' (row 9's dead vendor).
        assert created.extraction_status == "partial"
        assert await _admin_check_in_status(admin, check_in_id) == "partial"
    finally:
        await _admin_delete_users(admin, a)
        await _admin_delete_exercises_named(admin, "zercher squat")  # see row 25's teardown
        await close_pool(pool)


# =====================================================================================
# Rows 28-29 — the alias amendment (Toby, 2026-07-16, amendment #2 on PR #36)
# =====================================================================================


# AC row 28 (NEW): "curls", "bicep curl" and "barbell curl" all resolve to the SAME
# `exercises` row — the CANONICAL `barbell curl`. `workout_sets.exercise_id` never points at
# an alias row.
#
# Two separable claims, and the second is the one with teeth:
#   (a) the three ids are EQUAL — common phrasing logs instead of dropping (the cost row 26
#       accepted for `zercher squat`, which amendment #2 refuses to accept for `curls`).
#   (b) that shared id is the CANONICAL row — its `canonical_id` is null.
# Without (b), an implementation whose `resolve_exercise` returned the alias row's OWN id
# would satisfy (a) perfectly — all three names agree — while pointing every set at an alias.
# The catalog then has two rows meaning "barbell curl" and sets scattered across both: that
# is exactly the fragmentation row 7 exists to prevent, walking back in through the door
# amendment #2 opened. So (b) asserts the id resolves to a row that IS a movement, and the
# alias-row assertion below asserts it is NOT the alias's own id — an alias is a signpost,
# never a destination.
#
# Driven through `create_check_in` for at least one name (as rows 24/26 do), because the
# claim "workout_sets.exercise_id never points at an alias" is about what gets STORED, and
# `resolve_exercise` alone cannot prove what the writer did with its return value.
@requires_rls_db
async def test_row28_an_alias_resolves_to_its_canonical_movement() -> None:
    from app.db.facts import resolve_exercise
    from app.db.pool import close_pool, create_pool
    from app.db.session import authed_conn
    from app.schemas.check_ins import CheckInCreate
    from app.services.check_ins import create_check_in

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        await _admin_set_weight_unit(admin, a, "kg")
        await _require_seeded_catalog(admin)

        # The canonical movement the aliases must point at. Named by row 28 itself, so
        # pinning it is the row's claim, not the oracle's invention.
        canonical = await _admin_exercise_row(admin, "barbell curl")
        assert canonical is not None, (
            "'barbell curl' must be seeded as a canonical movement — row 28 names it as the "
            "target the alias `curls` resolves to"
        )
        assert canonical["canonical_id"] is None, (
            "'barbell curl' is the CANONICAL row in row 28's example; it must not itself be "
            "an alias pointing somewhere else"
        )
        canonical_id = canonical["id"]
        before = await _admin_count_exercises(admin)

        # ---- (a) all three spellings resolve to the SAME id ----
        async with authed_conn(pool, a) as conn:
            via_alias = await resolve_exercise(conn, "curls")
            via_other_alias = await resolve_exercise(conn, "bicep curl")
            via_canonical = await resolve_exercise(conn, "barbell curl")

        assert via_alias == via_canonical, "'curls' must resolve to the canonical barbell curl"
        assert via_other_alias == via_canonical, "'bicep curl' must resolve to the same row"

        # ---- (b) ...and that id is the CANONICAL row, not an alias's own id ----
        assert via_canonical == canonical_id
        resolved = await _admin_exercise_row(admin, "barbell curl")
        assert resolved is not None
        assert resolved["canonical_id"] is None, (
            "resolve_exercise returned a row that is itself an ALIAS — sets would point at a "
            "signpost, fragmenting the catalog exactly as row 7 forbids"
        )

        # The alias rows exist, are DISTINCT rows, and are not what anything resolves to.
        # This is what makes (b) non-vacuous: it proves `curls` really is a separate alias
        # row that got followed, rather than a second name on the canonical row.
        alias = await _admin_exercise_row(admin, "curls")
        assert alias is not None, "'curls' must be seeded as an alias row"
        assert alias["canonical_id"] == canonical_id  # the signpost points at the movement
        assert alias["id"] != canonical_id  # it is its own row...
        assert via_alias != alias["id"]  # ...and resolving `curls` never returns it

        # ---- the stored end: workout_sets.exercise_id points at the canonical movement ----
        created = await create_check_in(
            pool,
            a,
            CheckInCreate(text="4x10 curls"),
            _FakeExtractor(_sets_facts("curls", sets=4, weight="40")),
        )
        check_in_id = created.id

        assert await _admin_set_exercise_ids(admin, check_in_id) == [canonical_id] * 4
        # A logged set is a SUCCESS — the whole point of amendment #2 is that "curls" no
        # longer drops to 'partial' the way row 26's genuinely-unknown `zercher squat` does.
        assert created.extraction_status == "done"
        assert await _admin_check_in_status(admin, check_in_id) == "done"

        # Following an alias is still a READ. Nothing was minted.
        assert await _admin_count_exercises(admin) == before
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# AC row 29 (NEW): extraction over text naming an UNKNOWN movement, run as `coach_app` ->
# the `exercises` row count is UNCHANGED. Aliases are seeded, never minted; the role still
# holds `select` only, and row 25's structural assertions still hold.
#
# WHERE THIS OVERLAPS ROW 25, STATED PLAINLY: "extraction over an unknown movement as
# `coach_app` leaves the total row count unchanged" is verbatim row 25, and re-asserting it
# alone would be a pure duplicate — a test whose only function is to fail twice. So this
# asserts the two things amendment #2 ADDS, which row 25 is structurally blind to:
#
#   1. THE ALIAS COUNT, not just the total. Amendment #2 introduces a second KIND of row.
#      "No new rows" and "no new aliases" are the same assertion today and stop being the
#      same the moment anything can mint — and a total count cannot distinguish a catalog
#      that grew a movement from one that grew an alias.
#
#   2. UPDATE and DELETE privilege, not just INSERT. This is the real hole. `canonical_id`
#      is a new WRITE VECTOR THAT DOES NOT CHANGE THE ROW COUNT: repointing an existing
#      row's `canonical_id` re-aims a name at a different movement — mislogging every future
#      set under it — while every count assertion in row 25 and above stays green. Row 25
#      asserts `has_table_privilege(... 'insert')` is false, which says nothing about update.
#      Row 29's "the role still holds `select` only" is the clause that closes this, so the
#      test asserts it as written: select yes, insert/update/delete no.
#
# Note the live probes here are sharper than row 25's insert probe, and for a reason worth
# recording. Row 25 had to assert `has_table_privilege` separately because an insert refused
# by RLS and an insert refused by a missing GRANT raise the SAME 42501 class, so the probe
# alone couldn't tell "no privilege" from "privilege, no policy". UPDATE does not collide
# that way: with the grant present and no permissive policy, RLS makes an update match ZERO
# ROWS SILENTLY — no error at all. So an InsufficientPrivilegeError from the update probe
# can only mean the grant is absent, which is precisely row 29's claim.
@requires_rls_db
async def test_row29_aliases_do_not_reopen_the_write_path() -> None:
    from app.db.pool import close_pool, create_pool
    from app.db.session import authed_conn
    from app.services.extraction import extract_and_store

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    unknown = "zercher squat"  # neither seeded nor aliased — row 26's accepted cost
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        await _admin_set_weight_unit(admin, a, "kg")
        await _require_seeded_catalog(admin)

        assert await _admin_exercise_id(admin, unknown) is None, (
            "'zercher squat' must be neither seeded nor aliased — row 29 needs a genuinely "
            "unknown movement, and row 26's accepted cost depends on this one staying so"
        )
        before_total = await _admin_count_exercises(admin)
        before_aliases = await _admin_count_alias_rows(admin)
        assert before_aliases > 0, (
            "the catalog carries ZERO alias rows — amendment #2's seed migration is the "
            "precondition for rows 24/28/29. Until it lands, 'no aliases were minted' is "
            "vacuously true and this row proves nothing."
        )

        # ---- The role holds `select` ONLY ----
        async with authed_conn(pool, a) as conn:
            # select: TRUE. Asserted explicitly, because "holds no write privilege" is also
            # satisfied by a role that holds NOTHING — against which every drop-and-count
            # assertion in this file passes while the product is entirely broken.
            assert (
                await conn.fetchval(
                    "select has_table_privilege('authenticated', 'public.exercises', 'select')"
                )
                is True
            ), "`authenticated` cannot READ the catalog — every name would drop"

            for privilege in ("insert", "update", "delete"):
                assert (
                    await conn.fetchval(
                        "select has_table_privilege('authenticated', 'public.exercises', $1)",
                        privilege,
                    )
                    is False
                ), (
                    f"`authenticated` holds {privilege.upper()} on public.exercises. Amendment "
                    "#2 added a lookup path; it must not have added a write path. UPDATE is "
                    "the one to watch: repointing `canonical_id` mislogs every future set "
                    "under that name WITHOUT changing any row count, so no count assertion "
                    "in this file would notice."
                )

        # ---- ...and that reading corresponds to reality, exercised as the role ----
        # A privilege audited but never exercised is a claim about a catalog view.
        alias = await _admin_exercise_row(admin, "curls")
        assert alias is not None
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            async with authed_conn(pool, a) as conn:
                await conn.execute(
                    "insert into public.exercises (name, canonical_id) values ($1, $2)",
                    "row29 alias minting probe",
                    alias["canonical_id"],
                )
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            async with authed_conn(pool, a) as conn:
                await conn.execute(
                    "update public.exercises set canonical_id = null where id = $1", alias["id"]
                )

        # The probes changed nothing: no row minted, and `curls` still points where it did.
        assert await _admin_count_exercises_matching(admin, "%row29%") == 0
        still = await _admin_exercise_row(admin, "curls")
        assert still is not None
        assert still["canonical_id"] == alias["canonical_id"]  # not re-aimed

        # ---- The end-to-end check: the real path over unknown text mints nothing ----
        check_in_id = await _seed_check_in(pool, a, "zercher squat 185 3x5")
        await extract_and_store(
            pool,
            a,
            check_in_id,
            "zercher squat 185 3x5",
            _FakeExtractor(_sets_facts(unknown, sets=3, weight="185")),
        )

        assert await _admin_count_exercises(admin) == before_total
        assert await _admin_count_alias_rows(admin) == before_aliases  # no alias minted either
        assert await _admin_exercise_id(admin, unknown) is None
        assert await _admin_count_exercises_matching(admin, "%zercher%") == 0
    finally:
        await _admin_delete_users(admin, a)
        # No-ops unless something regressed — and if one ever stops being a no-op, that IS
        # the bug this row is about. See row 25's teardown.
        await _admin_delete_exercises_named(admin, unknown)
        await _admin_delete_exercises_named(admin, "row29 alias minting probe")
        await close_pool(pool)


# AC row 8 (real half): a re-run REPLACES the derived rows rather than duplicating them.
# There is no unique constraint on the fact tables, so a re-run that only inserts silently
# doubles every set — a row COUNT is the only thing that catches it, and only a real DB
# has row counts.
@requires_rls_db
async def test_row8_rerun_replaces_facts_and_does_not_duplicate() -> None:
    from app.db.pool import close_pool, create_pool
    from app.services.extraction import extract_and_store

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        await _admin_set_weight_unit(admin, a, "kg")
        check_in_id = await _seed_check_in(pool, a, "bench 135 4x8")

        await extract_and_store(
            pool, a, check_in_id, "bench 135 4x8", _FakeExtractor(_bench_facts(4))
        )
        assert await _admin_count_facts(admin, "workout_sets", check_in_id) == 4

        # Re-run with the SAME facts: still 4, not 8.
        await extract_and_store(
            pool, a, check_in_id, "bench 135 4x8", _FakeExtractor(_bench_facts(4))
        )
        assert await _admin_count_facts(admin, "workout_sets", check_in_id) == 4

        # Re-run with FEWER facts: the old rows are gone, not left behind.
        await extract_and_store(
            pool, a, check_in_id, "bench 135 1x8", _FakeExtractor(_bench_facts(1))
        )
        assert await _admin_count_facts(admin, "workout_sets", check_in_id) == 1
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)
