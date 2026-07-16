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
behaviour: "ONE exercises row" (row 7) and "rejects the name" (row 13) are what
`public.resolve_exercise` does, and "not duplicated" (row 8) is a row count. A fake pool
asserting those would only be asserting about the fake. These add the real proof; they do
not replace or weaken the section-A tests.
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
    from app.services.extraction import extract_and_store

    from app.db.pool import close_pool, create_pool

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
    from app.services.extraction import extract_and_store

    from app.db.pool import close_pool, create_pool
    from app.services.check_ins import list_check_ins

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


# AC row 7 (real half): "Bench Press" then "bench press" -> ONE exercises row, both sets
# point at it. `exercises.name` is a case-SENSITIVE unique index today, so without the
# guard's normalization the catalog fragments on day one. Only the real
# `public.resolve_exercise` can prove this.
@requires_rls_db
async def test_row7_resolve_exercise_folds_casing_to_one_row() -> None:
    from app.db.pool import close_pool, create_pool
    from app.db.session import authed_conn

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        async with authed_conn(pool, a) as conn:
            first = await conn.fetchval("select public.resolve_exercise($1)", "Bench Press")
            second = await conn.fetchval("select public.resolve_exercise($1)", "bench press")
            spaced = await conn.fetchval("select public.resolve_exercise($1)", "  Bench   Press  ")

        assert first is not None
        assert second == first  # case folded
        assert spaced == first  # trimmed + whitespace collapsed

        conn = await asyncpg.connect(admin)
        try:
            count = await conn.fetchval(
                "select count(*) from public.exercises where lower(name) = 'bench press'"
            )
        finally:
            await conn.close()
        assert count == 1  # ONE row, not three
    finally:
        await _admin_delete_users(admin, a)
        await _admin_delete_exercises_named(admin, "bench press", "Bench Press")
        await close_pool(pool)


# AC row 13 (real half): the guard REJECTS a name that is not letters/spaces/hyphens or is
# outside length 1-64, returning NULL and writing nothing. This is the leak the whole
# design exists to prevent: `exercises` is ownerless and shared, so an email address (or
# any user data) must never land in it.
@requires_rls_db
async def test_row13_resolve_exercise_rejects_names_outside_the_charset_and_length() -> None:
    from app.db.pool import close_pool, create_pool
    from app.db.session import authed_conn

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    leaky = "bench press — hmu at toby@gmail.com"
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        async with authed_conn(pool, a) as conn:
            assert await conn.fetchval("select public.resolve_exercise($1)", leaky) is None
            assert await conn.fetchval("select public.resolve_exercise($1)", "") is None
            assert await conn.fetchval("select public.resolve_exercise($1)", "   ") is None
            assert await conn.fetchval("select public.resolve_exercise($1)", "x" * 65) is None
            assert await conn.fetchval("select public.resolve_exercise($1)", "squat 5x5") is None
            # ...and the sanctioned shape is accepted, so the guard is not simply "reject all".
            assert await conn.fetchval("select public.resolve_exercise($1)", "x" * 64) is not None
            assert await conn.fetchval("select public.resolve_exercise($1)", "pull-up") is not None

        conn = await asyncpg.connect(admin)
        try:
            leaked = await conn.fetchval(
                "select count(*) from public.exercises where name like '%toby@gmail.com%'"
            )
        finally:
            await conn.close()
        assert leaked == 0  # the address never reached the shared catalog
    finally:
        await _admin_delete_users(admin, a)
        await _admin_delete_exercises_named(admin, "x" * 64, "pull-up")
        await close_pool(pool)


# AC row 8 (real half): a re-run REPLACES the derived rows rather than duplicating them.
# There is no unique constraint on the fact tables, so a re-run that only inserts silently
# doubles every set — a row COUNT is the only thing that catches it, and only a real DB
# has row counts.
@requires_rls_db
async def test_row8_rerun_replaces_facts_and_does_not_duplicate() -> None:
    from app.services.extraction import extract_and_store

    from app.db.pool import close_pool, create_pool

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
