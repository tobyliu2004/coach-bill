"""Oracle suite for issue #37 — least-privilege lockdown.

Commit #1 on `feat/37-least-privilege-revoke-excess`, written BEFORE the migration
exists. It encodes the Toby-approved correctness table (the acceptance criteria in the
issue), one test per row, and grades the not-yet-written migration — it is never a
photograph of whatever the migration ends up doing.

The change under test IS database state: a migration that REVOKEs the four excess table
privileges (TRUNCATE + REFERENCES + TRIGGER + MAINTENANCE) from `anon`, `authenticated`
and `service_role` on all 8 public tables, and rewrites the `ALTER DEFAULT PRIVILEGES`
template so a 9th table cannot silently reacquire them. There is nothing to fake, so there
are NO pure-unit tests here: EVERY test is real-DB gated with `@requires_rls_db`. Plain
`uv run pytest` skips the whole file — that is expected and correct.

The authoritative probe is `has_table_privilege` / `has_column_privilege` — it reads the
exact grant catalog Postgres itself consults and that the migration changes. The catalog
reads (A1-A4, B1, C1-C4, D1-D2) are the PRIMARY assertions; the live TRUNCATE attempts
(A5-A7) are CORROBORATION that the catalog reading matches what the database actually does.

For TRUNCATE there is NO 42501 ambiguity: RLS never governs TRUNCATE, so a raised
`InsufficientPrivilegeError` on a truncate can ONLY mean the grant is absent — never "grant
present but no row policy". That is why the live truncate probes are a clean corroboration.

Deliberately NOT probed live: an `anon` TRUNCATE attempt. `coach_app` cannot `set role anon`
(it is not a member), so `anon` is proven exclusively via the catalog reads (A1/B1/C1) — the
same fact Postgres consults. This is intentional, not a gap.

Gated exactly like test_rls_identity.py / test_extraction_db.py, and for the same reason:
`.env` is production and connects as the BYPASSRLS `postgres` role, against which several of
these assertions would be a false green.
  - RLS_DATABASE_URL       — the app pool as the non-BYPASSRLS login role `coach_app` (which
                             INHERITS `authenticated`); the code under test uses it. A5/A6
                             run live against it.
  - RLS_ADMIN_DATABASE_URL — a privileged `postgres` connection used to seed auth.users, to
                             read the grant catalog for ANY role via has_table_privilege
                             (which reads the catalog regardless of the connected role), and
                             to run D1/D2's rolled-back CREATE TABLE.
The whole suite skips unless RLS_DATABASE_URL is set; if it is set but RLS_ADMIN_DATABASE_URL
is missing, it fails loudly rather than pretending to pass.
"""

import os
import uuid

import asyncpg
import pytest

requires_rls_db = pytest.mark.skipif(
    not os.getenv("RLS_DATABASE_URL"),
    reason="RLS_DATABASE_URL not set; least-privilege real-DB suite skipped",
)

# All 8 public tables. Every "×8" in the AC table is all of these, not a sample. Fixed
# tuple, so f-stringing a member into SQL (for TRUNCATE) is safe — it is never client input.
TABLES = (
    "profiles",
    "check_ins",
    "exercises",
    "workout_sets",
    "nutrition_entries",
    "sleep_entries",
    "bodyweight_entries",
    "coach_messages",
)

# The four roles under test. `coach_app` is the login role prod uses; it INHERITS
# `authenticated`, so a privilege left on `authenticated` is one `coach_app` still wields.
ROLES = ("anon", "authenticated", "service_role", "coach_app")

# The four excess privileges the migration strips. TRUNCATE is section A's whole subject;
# the other three are section B.
EXCESS_NON_TRUNCATE = ("references", "trigger", "maintain")
ALL_EXCESS = ("truncate", *EXCESS_NON_TRUNCATE)

# Every table-level verb has_table_privilege understands, for the "holds nothing" controls.
ALL_VERBS = (
    "select",
    "insert",
    "update",
    "delete",
    "truncate",
    "references",
    "trigger",
    "maintain",
)

# C3's verified end-state: EXACTLY the app verbs `authenticated` keeps, table by table.
# Only the four data verbs appear here; truncate/references/trigger/maintain are asserted
# False for every table separately (the "excess is gone" half of the same control).
C3_APP_VERBS: dict[str, dict[str, bool]] = {
    "check_ins": {"select": True, "insert": True, "update": False, "delete": True},
    "profiles": {"select": True, "insert": False, "update": True, "delete": False},
    "exercises": {"select": True, "insert": False, "update": False, "delete": False},
    "workout_sets": {"select": True, "insert": True, "update": False, "delete": True},
    "nutrition_entries": {"select": True, "insert": True, "update": False, "delete": True},
    "sleep_entries": {"select": True, "insert": True, "update": False, "delete": True},
    "bodyweight_entries": {"select": True, "insert": True, "update": False, "delete": True},
    "coach_messages": {"select": False, "insert": False, "update": False, "delete": False},
}


def _require_admin_dsn() -> str:
    """The privileged fixture DSN, or fail loudly (never silently) if it's missing."""
    dsn = os.getenv("RLS_ADMIN_DATABASE_URL")
    if not dsn:
        pytest.fail(
            "RLS_DATABASE_URL is set but RLS_ADMIN_DATABASE_URL is not; the real-DB suite "
            "needs a privileged connection to read the grant catalog for every role, seed "
            "auth.users, and run D1/D2's rolled-back CREATE TABLE."
        )
    return dsn


async def _has_table_priv(conn: asyncpg.Connection, role: str, table: str, priv: str) -> bool:
    """`has_table_privilege(role, public.<table>, priv)` — the authoritative grant read.

    Runs over a privileged connection but names the ROLE explicitly, so it reads that role's
    grants regardless of who is connected.
    """
    result: bool = await conn.fetchval(
        "select has_table_privilege($1, $2, $3)", role, f"public.{table}", priv
    )
    return result


async def _has_column_priv(
    conn: asyncpg.Connection, role: str, table: str, column: str, priv: str
) -> bool:
    result: bool = await conn.fetchval(
        "select has_column_privilege($1, $2, $3, $4)", role, f"public.{table}", column, priv
    )
    return result


async def _admin_seed_users(admin_dsn: str, *user_ids: uuid.UUID) -> None:
    """Insert minimal auth.users rows (the app role cannot); the trigger makes profiles."""
    conn = await asyncpg.connect(admin_dsn)
    try:
        for uid in user_ids:
            await conn.execute(
                "insert into auth.users (id, email) values ($1, $2) on conflict do nothing",
                uid,
                f"{uid}@priv.test",
            )
    finally:
        await conn.close()


async def _admin_delete_users(admin_dsn: str, *user_ids: uuid.UUID) -> None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute("delete from auth.users where id = any($1::uuid[])", list(user_ids))
    finally:
        await conn.close()


async def _admin_count_check_in(admin_dsn: str, check_in_id: uuid.UUID) -> int:
    """Whether a specific check-in row still exists, read from OUTSIDE RLS.

    A7 asserts A's row survives a truncate attempt; counting it through A's own policy would
    ask the mechanism under test to grade itself.
    """
    conn = await asyncpg.connect(admin_dsn)
    try:
        count: int = await conn.fetchval(
            "select count(*) from public.check_ins where id = $1", check_in_id
        )
        return count
    finally:
        await conn.close()


async def _admin_seed_exercise(admin_dsn: str, name: str) -> uuid.UUID:
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


async def _admin_delete_exercise(admin_dsn: str, ex_id: uuid.UUID) -> None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute("delete from public.exercises where id = $1", ex_id)
    finally:
        await conn.close()


async def _seed_check_in(pool: asyncpg.Pool, user_id: uuid.UUID, text: str) -> uuid.UUID:
    """Write a check-in under THAT user's OWN identity (RLS with-check allows it)."""
    from app.db.session import authed_conn

    async with authed_conn(pool, user_id) as conn:
        cid: uuid.UUID = await conn.fetchval(
            "insert into public.check_ins (user_id, raw_text, source, entry_date) "
            "values ($1, $2, 'text', current_date) returning id",
            user_id,
            text,
        )
        return cid


# =====================================================================================
# A — the hole is closed (primary claim): no role holds TRUNCATE on any of the 8 tables
# =====================================================================================


# AC row A1: has_table_privilege('anon', T, 'truncate') ×8 -> False ×8. This is the
# headline hole: `anon` holds TRUNCATE without even holding SELECT, so an unauthenticated
# caller could wipe a table it cannot read.
@requires_rls_db
async def test_a1_anon_holds_no_truncate_on_any_table() -> None:
    conn = await asyncpg.connect(_require_admin_dsn())
    try:
        for table in TABLES:
            assert await _has_table_priv(conn, "anon", table, "truncate") is False, (
                f"anon still holds TRUNCATE on public.{table}"
            )
    finally:
        await conn.close()


# AC row A2: has_table_privilege('authenticated', T, 'truncate') ×8 -> False ×8.
@requires_rls_db
async def test_a2_authenticated_holds_no_truncate_on_any_table() -> None:
    conn = await asyncpg.connect(_require_admin_dsn())
    try:
        for table in TABLES:
            assert await _has_table_priv(conn, "authenticated", table, "truncate") is False, (
                f"authenticated still holds TRUNCATE on public.{table}"
            )
    finally:
        await conn.close()


# AC row A3: has_table_privilege('service_role', T, 'truncate') ×8 -> False ×8. The unused
# role is in scope: least privilege strips it too, so it can never become a live hole.
@requires_rls_db
async def test_a3_service_role_holds_no_truncate_on_any_table() -> None:
    conn = await asyncpg.connect(_require_admin_dsn())
    try:
        for table in TABLES:
            assert await _has_table_priv(conn, "service_role", table, "truncate") is False, (
                f"service_role still holds TRUNCATE on public.{table}"
            )
    finally:
        await conn.close()


# AC row A4: has_table_privilege('coach_app', T, 'truncate') ×8 -> False ×8. coach_app
# INHERITS authenticated, so this catches a revoke that fixed `authenticated` but left the
# privilege reachable through the login role prod actually connects as.
@requires_rls_db
async def test_a4_coach_app_holds_no_truncate_on_any_table() -> None:
    conn = await asyncpg.connect(_require_admin_dsn())
    try:
        for table in TABLES:
            assert await _has_table_priv(conn, "coach_app", table, "truncate") is False, (
                f"coach_app (inheriting authenticated) still holds TRUNCATE on public.{table}"
            )
    finally:
        await conn.close()


# AC row A5: live, as `authenticated` (inside authed_conn's `set local role authenticated`):
# TRUNCATE public.<T> ×8 -> raises InsufficientPrivilegeError. Live corroboration that the
# catalog read (A2/A4) matches reality. TRUNCATE is never governed by RLS, so a raised
# InsufficientPrivilegeError can ONLY mean the grant is absent. Each table runs in its own
# authed_conn transaction so a raise on one does not poison the next.
@requires_rls_db
async def test_a5_authenticated_truncate_is_refused_live() -> None:
    from app.db.pool import close_pool, create_pool
    from app.db.session import authed_conn

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)  # a clean identity for the transaction to carry
        for table in TABLES:
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                async with authed_conn(pool, a) as conn:
                    await conn.execute(f"truncate public.{table}")
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# AC row A6: live, as RAW `coach_app` (a plain pool.acquire() on the RLS_DATABASE_URL pool,
# NO `set role`): TRUNCATE public.check_ins -> raises InsufficientPrivilegeError. This is the
# role prod logs in as, with no identity set at all — it must not inherit TRUNCATE from
# `authenticated`.
@requires_rls_db
async def test_a6_raw_coach_app_truncate_is_refused_live() -> None:
    from app.db.pool import close_pool, create_pool

    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await conn.execute("truncate public.check_ins")
    finally:
        await close_pool(pool)


# AC row A7: data survives. Seed user A a check-in, attempt a truncate as authenticated (which
# must fail), then assert the row is STILL present. Proves the closed hole actually protects
# data, not just the catalog bit. The row is counted from OUTSIDE RLS.
@requires_rls_db
async def test_a7_data_survives_a_refused_truncate() -> None:
    from app.db.pool import close_pool, create_pool
    from app.db.session import authed_conn

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        cid = await _seed_check_in(pool, a, "A's row that must survive a wipe attempt")
        assert await _admin_count_check_in(admin, cid) == 1  # precondition: it's really there

        async with authed_conn(pool, a) as conn:
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await conn.execute("truncate public.check_ins")

        assert await _admin_count_check_in(admin, cid) == 1  # the truncate did not wipe it
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# =====================================================================================
# B — the other three excess privileges are gone (REFERENCES, TRIGGER, MAINTENANCE)
# =====================================================================================


# AC row B1: has_table_privilege(role, T, p) for role ∈ {anon, authenticated, service_role,
# coach_app}, p ∈ {references, trigger, maintain}, ×8 -> False (all). One assertion per
# (role, table, verb) with a message naming all three, because a revoke can be forgotten one
# verb, one table, or one role at a time.
@requires_rls_db
async def test_b1_no_role_holds_references_trigger_or_maintain() -> None:
    conn = await asyncpg.connect(_require_admin_dsn())
    try:
        for role in ROLES:
            for table in TABLES:
                for verb in EXCESS_NON_TRUNCATE:
                    assert await _has_table_priv(conn, role, table, verb) is False, (
                        f"{role} still holds {verb.upper()} on public.{table}"
                    )
    finally:
        await conn.close()


# =====================================================================================
# C — least-privilege end-state (positive controls: did NOT over-revoke; app still works)
# =====================================================================================


# AC row C1: `anon` holds NOTHING — every verb in {select,insert,update,delete,truncate,
# references,trigger,maintain}, ×8 -> False (all). anon is the pre-login role; it should have
# no table access whatsoever.
@requires_rls_db
async def test_c1_anon_holds_no_table_privilege_at_all() -> None:
    conn = await asyncpg.connect(_require_admin_dsn())
    try:
        for table in TABLES:
            for verb in ALL_VERBS:
                assert await _has_table_priv(conn, "anon", table, verb) is False, (
                    f"anon still holds {verb.upper()} on public.{table}"
                )
    finally:
        await conn.close()


# AC row C2: `service_role` holds NOTHING — same full verb set, ×8 -> False (all).
@requires_rls_db
async def test_c2_service_role_holds_no_table_privilege_at_all() -> None:
    conn = await asyncpg.connect(_require_admin_dsn())
    try:
        for table in TABLES:
            for verb in ALL_VERBS:
                assert await _has_table_priv(conn, "service_role", table, verb) is False, (
                    f"service_role still holds {verb.upper()} on public.{table}"
                )
    finally:
        await conn.close()


# AC row C3: `authenticated` keeps EXACTLY its app verbs per the verified matrix, and NONE of
# the four excess. This is the "we did not over-revoke AND we removed the excess" control, so
# it asserts BOTH directions: every app verb is exactly its expected True/False, AND every one
# of truncate/references/trigger/maintain is False, on every table. A revoke that stripped a
# real app verb (over-revoke) fails the first half; one that left an excess privilege fails the
# second.
@requires_rls_db
async def test_c3_authenticated_keeps_exactly_its_app_verbs() -> None:
    conn = await asyncpg.connect(_require_admin_dsn())
    try:
        for table, verbs in C3_APP_VERBS.items():
            for verb, expected in verbs.items():
                actual = await _has_table_priv(conn, "authenticated", table, verb)
                assert actual is expected, (
                    f"authenticated {verb.upper()} on public.{table}: "
                    f"expected {expected}, got {actual}"
                )
            for verb in ALL_EXCESS:
                assert await _has_table_priv(conn, "authenticated", table, verb) is False, (
                    f"authenticated still holds excess {verb.upper()} on public.{table}"
                )
    finally:
        await conn.close()


# AC row C4: the column-level grant is intact. `authenticated` may UPDATE
# check_ins.extraction_status (the one column the app writes on that table) but NOT
# check_ins.raw_text. This is the grant that would be collateral damage if a blunt
# `revoke ... on check_ins` also swept the column privilege — so it is a distinct control.
@requires_rls_db
async def test_c4_column_update_grant_on_check_ins_is_intact() -> None:
    conn = await asyncpg.connect(_require_admin_dsn())
    try:
        assert (
            await _has_column_priv(
                conn, "authenticated", "check_ins", "extraction_status", "update"
            )
            is True
        ), "authenticated lost UPDATE on check_ins.extraction_status — the app can't stamp status"
        assert (
            await _has_column_priv(conn, "authenticated", "check_ins", "raw_text", "update")
            is False
        ), "authenticated gained UPDATE on check_ins.raw_text — it must never edit the user's words"
    finally:
        await conn.close()


# AC row C5: the owner path still works end-to-end THROUGH THE SERVICES (no regression). Create
# + list + delete own check-in; read + update own profile; read `exercises`. Uses the same
# service/db seams the RLS suite uses, so a revoke that broke a real app verb (over-revoke)
# surfaces here as a failing request, not a silent privilege bit.
@requires_rls_db
async def test_c5_owner_path_still_works_end_to_end() -> None:
    from app.db.check_ins import delete_check_in
    from app.db.facts import resolve_exercise
    from app.db.pool import close_pool, create_pool
    from app.db.profiles import get_profile, update_profile
    from app.db.session import authed_conn
    from app.schemas.check_ins import CheckInCreate
    from app.schemas.extraction import ExtractedFacts
    from app.services.check_ins import create_check_in, list_check_ins

    # This row is about privileges, not extraction, so the extractor does nothing (no network,
    # no facts) — copied from test_rls_identity's owner-path test.
    class _NullExtractor:
        async def extract(self, text: str) -> ExtractedFacts:
            return ExtractedFacts()

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    ex_name = f"c5-read-probe-{uuid.uuid4().hex}"
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    ex_id: uuid.UUID | None = None
    try:
        await _admin_seed_users(admin, a)  # trigger creates the profile row
        ex_id = await _admin_seed_exercise(admin, ex_name)

        # create + list own check-in (SELECT + INSERT on check_ins, via the service)
        created = await create_check_in(pool, a, CheckInCreate(text="owner note"), _NullExtractor())
        listed_ids = [r.id for r in await list_check_ins(pool, a)]
        assert created.id in listed_ids
        assert created.raw_text == "owner note"

        # read + update own profile (SELECT + UPDATE on profiles, via the db layer)
        updated = await update_profile(
            pool,
            a,
            display_name="Owner",
            weight_unit="kg",
            goal="cut to 175",
            timezone=None,
            set_consent=True,
        )
        assert updated is not None
        assert updated["display_name"] == "Owner"
        reread = await get_profile(pool, a)
        assert reread is not None
        assert reread["display_name"] == "Owner"

        # read exercises (SELECT on the shared catalog, via resolve_exercise) — the seeded
        # row resolves to its own id, proving the SELECT grant was NOT over-revoked.
        async with authed_conn(pool, a) as conn:
            resolved = await resolve_exercise(conn, ex_name)
        assert resolved == ex_id

        # delete own check-in (DELETE on check_ins, via the db layer)
        assert await delete_check_in(pool, a, created.id) is True
        assert all(r.id != created.id for r in await list_check_ins(pool, a))
    finally:
        if ex_id is not None:
            await _admin_delete_exercise(admin, ex_id)
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# =====================================================================================
# D — the future-table template is fixed (a 9th table can't silently reacquire the excess)
# =====================================================================================


# AC row D1: as `postgres` (admin conn), inside a transaction we ROLL BACK, CREATE a fresh
# table, then has_table_privilege('anon'/'authenticated'/'service_role', tmp, 'truncate') ->
# False for all three. This proves the `ALTER DEFAULT PRIVILEGES` template no longer hands
# TRUNCATE to a brand-new table. Rolled back, so the tmp table never persists.
@requires_rls_db
async def test_d1_new_table_grants_no_truncate_by_default() -> None:
    conn = await asyncpg.connect(_require_admin_dsn())
    tmp = f"tmp_priv_{uuid.uuid4().hex}"
    try:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(
                f"create table public.{tmp} (id uuid primary key default gen_random_uuid())"
            )
            for role in ("anon", "authenticated", "service_role"):
                assert await _has_table_priv(conn, role, tmp, "truncate") is False, (
                    f"a brand-new table hands {role} TRUNCATE — the default-privilege "
                    "template still leaks the excess to future tables"
                )
        finally:
            await tx.rollback()  # the tmp table never persists
    finally:
        await conn.close()


# AC row D2: same rolled-back CREATE, but the other three excess verbs — references / trigger
# / maintain -> False for anon / authenticated / service_role. Same template, same fix.
@requires_rls_db
async def test_d2_new_table_grants_no_references_trigger_or_maintain_by_default() -> None:
    conn = await asyncpg.connect(_require_admin_dsn())
    tmp = f"tmp_priv_{uuid.uuid4().hex}"
    try:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(
                f"create table public.{tmp} (id uuid primary key default gen_random_uuid())"
            )
            for role in ("anon", "authenticated", "service_role"):
                for verb in EXCESS_NON_TRUNCATE:
                    assert await _has_table_priv(conn, role, tmp, verb) is False, (
                        f"a brand-new table hands {role} {verb.upper()} — the default-privilege "
                        "template still leaks the excess to future tables"
                    )
        finally:
            await tx.rollback()
    finally:
        await conn.close()
