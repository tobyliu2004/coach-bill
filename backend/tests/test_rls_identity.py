"""Oracle suite for issue #24 — RLS-respecting DB role + per-transaction identity.

This file is commit #1 on `feat/24-rls-respecting-role`, written BEFORE any
implementation exists. It encodes the 12 Toby-approved promises (the acceptance
criteria in the issue), one integration test per promise, and never a photograph of
whatever the code ends up doing. At oracle time `from app.db.session import authed_conn`
raises ImportError — that is the *correct* failure. When the build lands `authed_conn`
and flips the db layer, these go green without being touched.

Two layers of coverage:

1. Pure-unit invariant tests (NO DB, always run). They prove the `authed_conn`
   MECHANISM through a recording fake connection: the transaction opens BEFORE any
   identity SQL, the claims JSON is bound as a parameter (never f-strung), the
   `set_config` precedes `set local role authenticated`, and the same conn is yielded to
   the caller. These underpin promises 3, 4, 5, 6 and 12 — the SET-LOCAL-in-a-transaction
   design is exactly what makes identity vanish on commit/rollback and never leak.

2. Real-DB integration tests (gated). They execute the actual RLS behaviour against a
   local Postgres reached as the dedicated **non-BYPASSRLS** login role. Only a real DB
   can prove fail-closed reads, cross-tenant rejection, and no-leak-on-reuse — a fake
   cannot run RLS.

Why its own env vars (never `.env`): `.env` is production and connects as the
BYPASSRLS `postgres` role, against which every RLS assertion here would be a false
green. This suite therefore gates on:
  - RLS_DATABASE_URL       — the app pool, connecting as the fail-closed non-BYPASSRLS
                             role (locally `coach_app`); the code under test uses it.
  - RLS_ADMIN_DATABASE_URL — a privileged local connection (postgres) used ONLY by
                             fixtures to seed `auth.users` / `exercises` and tear down.
The whole real-DB layer skips unless RLS_DATABASE_URL is set; if it is set but
RLS_ADMIN_DATABASE_URL is missing, the test fails loudly rather than pretending to pass.

The fail-closed role is load-bearing: with a non-BYPASSRLS role, a query that never sets
identity lands on a powerless role and returns ZERO rows. Against a BYPASSRLS role these
tests could not distinguish "isolation works" from "isolation was never engaged".
"""

import json
import os
import uuid
from typing import Any

import asyncpg
import pytest

# =====================================================================================
# Layer 1 — pure-unit mechanism tests (no DB, always run)
# =====================================================================================
#
# A recording fake conn proves the ORDER and SHAPE of what authed_conn does, without a
# database. It records both transaction lifecycle markers and every (query, args) so we
# can assert the transaction opens before any identity SQL and that the claims JSON is a
# bind arg. We record generically across execute/fetchval/fetch and assert on the ordered
# (query, args) list, NOT on which method name — so the implementation isn't over-constrained.

_TXN_ENTER = "__txn_enter__"
_TXN_EXIT = "__txn_exit__"
_YIELD = "__yield__"


class _RecordingTxn:
    """Async context manager standing in for `conn.transaction()`."""

    def __init__(self, conn: "_RecordingConn") -> None:
        self._conn = conn

    async def __aenter__(self) -> "_RecordingTxn":
        self._conn.events.append((_TXN_ENTER, ()))
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        self._conn.events.append((_TXN_EXIT, ()))
        return False


class _RecordingConn:
    """Records the ordered sequence of transaction markers and (query, args) calls."""

    def __init__(self) -> None:
        self.events: list[tuple[str, tuple[Any, ...]]] = []

    def transaction(self) -> _RecordingTxn:
        return _RecordingTxn(self)

    async def execute(self, query: str, *args: Any) -> str:
        self.events.append((query, args))
        return "OK"

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.events.append((query, args))
        return None

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        self.events.append((query, args))
        return []


class _RecordingAcquire:
    def __init__(self, conn: _RecordingConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _RecordingConn:
        return self._conn

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _RecordingPool:
    def __init__(self, conn: _RecordingConn) -> None:
        self._conn = conn

    def acquire(self) -> _RecordingAcquire:
        return _RecordingAcquire(self._conn)


async def _drive_authed_conn(uid: uuid.UUID) -> tuple[_RecordingConn, _RecordingConn]:
    """Run `authed_conn(pool, uid)` against the recording fake; return (conn, yielded).

    Imports lazily so this file collects cleanly while `app.db.session` does not yet
    exist — at oracle time this raises ImportError, which surfaces as the unit tests
    FAILING (the correct red), not as a collection error that would also swallow the
    gated real-DB skips.
    """
    from app.db.session import authed_conn

    conn = _RecordingConn()
    pool = _RecordingPool(conn)
    yielded: _RecordingConn | None = None
    async with authed_conn(pool, uid) as c:  # type: ignore[arg-type]
        yielded = c
        conn.events.append((_YIELD, ()))
    assert yielded is not None
    return conn, yielded


def _sql_calls(conn: _RecordingConn) -> list[tuple[str, tuple[Any, ...]]]:
    """Just the SQL (query, args), with transaction/yield markers filtered out."""
    return [(q, a) for q, a in conn.events if q not in (_TXN_ENTER, _TXN_EXIT, _YIELD)]


# Promises 5, 6, 12 (mechanism): SET LOCAL / set_config(...,true) are transaction-scoped,
# so identity vanishes on commit/rollback and cannot leak to the next borrower — but ONLY
# if the transaction is open before anything is set. Assert the transaction is entered
# BEFORE the first identity SQL runs.
async def test_transaction_opens_before_any_identity_sql() -> None:
    conn, _ = await _drive_authed_conn(uuid.uuid4())

    txn_enter_idx = next(i for i, (q, _a) in enumerate(conn.events) if q == _TXN_ENTER)
    first_sql_idx = next(
        i for i, (q, _a) in enumerate(conn.events) if q not in (_TXN_ENTER, _TXN_EXIT, _YIELD)
    )
    assert txn_enter_idx < first_sql_idx  # nothing is set outside the transaction


# Promise 3 (mechanism) + backend rule 6: the JWT claims JSON — {"sub": <uuid>,
# "role": "authenticated"} — is passed as a BIND ARG to set_config, never f-strung into
# the SQL text. This is what makes `select auth.uid()` equal the JWT sub downstream.
async def test_claims_json_passed_as_bind_arg_not_fstrung() -> None:
    uid = uuid.uuid4()
    conn, _ = await _drive_authed_conn(uid)

    set_config = next((q, a) for q, a in _sql_calls(conn) if "set_config" in q.lower())
    query, args = set_config
    assert "request.jwt.claims" in query.lower()
    expected_claims = json.dumps({"sub": str(uid), "role": "authenticated"})
    assert expected_claims in args  # the JSON is a parameter ($1), not interpolated
    assert str(uid) not in query  # the uuid is never f-strung into the statement


# Promise 3/4 (mechanism): the recorded order is set_config(claims) THEN
# `set local role authenticated` — claims must exist before the role switch consults them.
async def test_set_config_precedes_set_role() -> None:
    conn, _ = await _drive_authed_conn(uuid.uuid4())
    calls = _sql_calls(conn)
    set_config_idx = next(i for i, (q, _a) in enumerate(calls) if "set_config" in q.lower())
    set_role_idx = next(
        i for i, (q, _a) in enumerate(calls) if q.strip().lower() == "set local role authenticated"
    )
    assert set_config_idx < set_role_idx


# Promise 5/12 (mechanism): authed_conn yields the very connection it acquired and set
# identity on, so the caller's queries run inside that same authed transaction.
async def test_authed_conn_yields_the_acquired_conn() -> None:
    conn, yielded = await _drive_authed_conn(uuid.uuid4())
    assert yielded is conn


# =====================================================================================
# Layer 2 — real-DB integration tests (gated; skip unless RLS_DATABASE_URL is set)
# =====================================================================================

requires_rls_db = pytest.mark.skipif(
    not os.getenv("RLS_DATABASE_URL"),
    reason="RLS_DATABASE_URL not set; RLS real-DB suite skipped",
)


def _require_admin_dsn() -> str:
    """The privileged fixture DSN, or fail loudly (never silently) if it's missing."""
    dsn = os.getenv("RLS_ADMIN_DATABASE_URL")
    if not dsn:
        pytest.fail(
            "RLS_DATABASE_URL is set but RLS_ADMIN_DATABASE_URL is not; the real-DB "
            "suite needs a privileged connection to seed auth.users / exercises."
        )
    return dsn


async def _admin_seed_users(admin_dsn: str, *user_ids: uuid.UUID) -> None:
    """Insert minimal auth.users rows (the app role cannot); the trigger makes profiles."""
    conn = await asyncpg.connect(admin_dsn)
    try:
        for uid in user_ids:
            await conn.execute(
                "insert into auth.users (id, email) values ($1, $2) on conflict do nothing",
                uid,
                f"{uid}@rls.test",
            )
    finally:
        await conn.close()


async def _admin_delete_users(admin_dsn: str, *user_ids: uuid.UUID) -> None:
    """Cascade-delete seeded users (removes their check_ins and the profiles trigger row)."""
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(
            "delete from auth.users where id = any($1::uuid[])",
            list(user_ids),
        )
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


# Promise 1: under A's identity, a DELIBERATELY UNFILTERED `select * from check_ins`
# returns ONLY A's rows — the RLS safety net, not the application WHERE clause.
@requires_rls_db
async def test_unfiltered_select_returns_only_a_rows() -> None:
    from app.db.session import authed_conn

    from app.db.pool import close_pool, create_pool

    admin = _require_admin_dsn()
    a, b = uuid.uuid4(), uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a, b)
        a1 = await _seed_check_in(pool, a, "A one")
        a2 = await _seed_check_in(pool, a, "A two")
        b1 = await _seed_check_in(pool, b, "B one")

        async with authed_conn(pool, a) as conn:
            rows = await conn.fetch("select * from public.check_ins")

        ids = {r["id"] for r in rows}
        assert ids == {a1, a2}  # exactly A's rows, nothing of B's
        assert b1 not in ids
        assert all(r["user_id"] == a for r in rows)  # every visible row is owned by A
    finally:
        await _admin_delete_users(admin, a, b)
        await close_pool(pool)


# Promise 2: the same unfiltered query returns EMPTY when A has no rows and B has data —
# never B's rows.
@requires_rls_db
async def test_unfiltered_select_empty_when_a_has_no_rows() -> None:
    from app.db.session import authed_conn

    from app.db.pool import close_pool, create_pool

    admin = _require_admin_dsn()
    a, b = uuid.uuid4(), uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a, b)
        await _seed_check_in(pool, b, "B only")  # A intentionally has no rows

        async with authed_conn(pool, a) as conn:
            rows = await conn.fetch("select * from public.check_ins")

        assert rows == []  # never B's rows
    finally:
        await _admin_delete_users(admin, a, b)
        await close_pool(pool)


# Promise 3: `select auth.uid()` inside a request's transaction equals the JWT sub (A's uuid).
@requires_rls_db
async def test_auth_uid_equals_jwt_sub() -> None:
    from app.db.session import authed_conn

    from app.db.pool import close_pool, create_pool

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        async with authed_conn(pool, a) as conn:
            observed = await conn.fetchval("select auth.uid()")
        assert observed == a
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# Promise 4: a transaction that NEVER sets identity -> auth.uid() is null AND zero rows
# (fail-closed). Meaningful ONLY because the connection role is non-BYPASSRLS.
@requires_rls_db
async def test_no_identity_is_fail_closed() -> None:
    from app.db.pool import close_pool, create_pool

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        await _seed_check_in(pool, a, "A has data that must stay hidden")

        # Raw acquire + explicit transaction, NO identity set at all.
        async with pool.acquire() as conn:
            async with conn.transaction():
                observed_uid = await conn.fetchval("select auth.uid()")
                count = await conn.fetchval("select count(*) from public.check_ins")

        assert observed_uid is None  # no identity
        assert count == 0  # non-BYPASSRLS role sees nothing without a matching policy
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# Promise 5: a reused PHYSICAL connection carries no leftover identity from the previous
# user. Deterministic via max_size=1 (forced reuse of the one connection). Sequence:
#   (a) authed_conn(A) -> auth.uid()==A
#   (b) raw acquire + explicit txn, no identity -> auth.uid() null AND count==0
#   (c) authed_conn(B) -> auth.uid()==B and sees only B's rows
@requires_rls_db
async def test_reused_connection_carries_no_leftover_identity() -> None:
    from app.db.session import authed_conn

    from app.db.pool import close_pool

    admin = _require_admin_dsn()
    a, b = uuid.uuid4(), uuid.uuid4()
    pool = await asyncpg.create_pool(os.environ["RLS_DATABASE_URL"], min_size=1, max_size=1)
    try:
        await _admin_seed_users(admin, a, b)
        b1 = await _seed_check_in(pool, b, "B one")

        # (a)
        async with authed_conn(pool, a) as conn:
            assert await conn.fetchval("select auth.uid()") == a

        # (b) same physical conn, no identity -> fully fail-closed
        async with pool.acquire() as conn:
            async with conn.transaction():
                assert await conn.fetchval("select auth.uid()") is None
                assert await conn.fetchval("select count(*) from public.check_ins") == 0

        # (c) same physical conn, now B's identity
        async with authed_conn(pool, b) as conn:
            assert await conn.fetchval("select auth.uid()") == b
            rows = await conn.fetch("select * from public.check_ins")
            assert {r["id"] for r in rows} == {b1}
            assert all(r["user_id"] == b for r in rows)
    finally:
        await _admin_delete_users(admin, a, b)
        await close_pool(pool)


# Promise 6: two concurrent users are isolated. NO separate test — this is a corollary of
# the SET LOCAL / set_config(...,true) mechanism (proven in the Layer 1 unit tests) plus
# promise 5's forced-reuse proof that identity never survives a connection hand-off. A
# live concurrency race here would be flaky and prove nothing the mechanism doesn't. (JC-1)


# Promise 7: an INSERT into check_ins with someone ELSE's user_id, done under A's identity,
# is REJECTED by the RLS `with check` policy -> raises, and nothing is written.
@requires_rls_db
async def test_insert_with_other_user_id_is_rejected() -> None:
    from app.db.session import authed_conn

    from app.db.pool import close_pool, create_pool

    admin = _require_admin_dsn()
    a, b = uuid.uuid4(), uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a, b)

        with pytest.raises(asyncpg.PostgresError) as exc:
            async with authed_conn(pool, a) as conn:
                await conn.execute(
                    "insert into public.check_ins (user_id, raw_text, source, entry_date) "
                    "values ($1, $2, 'text', current_date)",
                    b,  # someone ELSE's id, under A's identity
                    "smuggled row",
                )
        assert "row-level security" in str(exc.value).lower()

        # Nothing was written: neither owner sees the row.
        async with authed_conn(pool, b) as conn:
            assert await conn.fetchval("select count(*) from public.check_ins") == 0
        async with authed_conn(pool, a) as conn:
            assert await conn.fetchval("select count(*) from public.check_ins") == 0
    finally:
        await _admin_delete_users(admin, a, b)
        await close_pool(pool)


# Promise 8: cross-tenant DELETE via the real service/db layer (which routes through
# authed_conn after the build). Seed A's check-in; delete_check_in(pool, B, a_id) -> False
# and A's row unchanged; then delete_check_in(pool, A, a_id) -> True and gone.
@requires_rls_db
async def test_cross_tenant_delete_leaves_a_row_unchanged() -> None:
    from app.db.check_ins import delete_check_in
    from app.db.pool import close_pool, create_pool
    from app.services.check_ins import list_check_ins

    admin = _require_admin_dsn()
    a, b = uuid.uuid4(), uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a, b)
        a_id = await _seed_check_in(pool, a, "A's private note")

        assert await delete_check_in(pool, b, a_id) is False  # B cannot touch A's row
        a_rows = await list_check_ins(pool, a)
        assert any(r.id == a_id for r in a_rows)  # unchanged

        assert await delete_check_in(pool, a, a_id) is True  # owner can
        a_rows_after = await list_check_ins(pool, a)
        assert all(r.id != a_id for r in a_rows_after)  # gone
    finally:
        await _admin_delete_users(admin, a, b)
        await close_pool(pool)


# Promise 9: no regression — the owner path still works end-to-end through the services.
# create_check_in then list_check_ins returns the created row; get_profile(pool, A) returns
# A's profile (auto-created by the auth.users trigger).
@requires_rls_db
async def test_owner_path_end_to_end() -> None:
    from app.db.pool import close_pool, create_pool
    from app.db.profiles import get_profile
    from app.schemas.check_ins import CheckInCreate
    from app.services.check_ins import create_check_in, list_check_ins

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)

        created = await create_check_in(pool, a, CheckInCreate(text="owner note"))
        listed_ids = [r.id for r in await list_check_ins(pool, a)]
        assert created.id in listed_ids
        assert created.raw_text == "owner note"

        profile = await get_profile(pool, a)
        assert profile is not None
        assert profile["id"] == a  # trigger-created, and visible to its owner under RLS
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# Promise 10: the ownerless `exercises` catalog is still readable under authed_conn(A).
@requires_rls_db
async def test_exercises_catalog_readable_under_identity() -> None:
    from app.db.session import authed_conn

    from app.db.pool import close_pool, create_pool

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    ex_name = f"rls-test-exercise-{uuid.uuid4()}"
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    ex_id: uuid.UUID | None = None
    try:
        await _admin_seed_users(admin, a)
        ex_id = await _admin_seed_exercise(admin, ex_name)

        async with authed_conn(pool, a) as conn:
            rows = await conn.fetch("select * from public.exercises")

        assert ex_id in {r["id"] for r in rows}  # shared catalog stays readable
    finally:
        if ex_id is not None:
            await _admin_delete_exercise(admin, ex_id)
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# Promise 11: the /health/db path still works — check_db does SELECT 1 (touches no table)
# on the app role with no identity set.
@requires_rls_db
async def test_health_check_db_works_without_identity() -> None:
    from app.db.pool import close_pool, create_pool
    from app.services.health import check_db

    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        result = await check_db(pool)
    finally:
        await close_pool(pool)

    assert result.ok is True
    assert result.db == "up"
    assert result.latency_ms >= 0


# Promise 12: a mid-request error rolls back and leaves the connection clean for the next
# borrower. Deterministic with max_size=1: authed_conn(A) then a statement that errors
# (select 1/0) aborts the txn; then authed_conn(B) on the SAME physical conn must work
# cleanly — auth.uid()==B and a normal query succeeds (no "current transaction is aborted").
@requires_rls_db
async def test_error_rolls_back_and_leaves_connection_clean() -> None:
    from app.db.session import authed_conn

    from app.db.pool import close_pool

    admin = _require_admin_dsn()
    a, b = uuid.uuid4(), uuid.uuid4()
    pool = await asyncpg.create_pool(os.environ["RLS_DATABASE_URL"], min_size=1, max_size=1)
    try:
        await _admin_seed_users(admin, a, b)

        with pytest.raises(asyncpg.PostgresError):
            async with authed_conn(pool, a) as conn:
                await conn.fetchval("select 1 / 0")  # aborts the transaction

        # Same physical conn, next borrower: must be clean, not a poisoned aborted txn.
        async with authed_conn(pool, b) as conn:
            assert await conn.fetchval("select auth.uid()") == b
            assert await conn.fetchval("select count(*) from public.check_ins") == 0
    finally:
        await _admin_delete_users(admin, a, b)
        await close_pool(pool)
