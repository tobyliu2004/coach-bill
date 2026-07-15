"""Behavior of POST/GET /check-ins and DELETE /check-ins/{id}, written before the code.

Two layers of coverage:

1. Fake-pool unit tests (rows 1-5, 7-8, 10-11, 13-15). Auth is faked by overriding the
   verified-user dependency; the pool is faked at the boundary. The fakes cannot execute
   SQL, so they prove the *contract*: status codes, validation, response shape, and that
   the db layer is invoked with the right statement + arguments (including user_id in the
   same statement — the security boundary). Copied in shape from test_profiles.py, but the
   check-in services make TWO sequential db calls (timezone read, then insert/list), so the
   fake conn returns primed values in order and records every (query, args).

2. Real-DB integration tests (rows 12, 9, 7 end-to-end), gated like test_health.py's. These
   are the ONLY place cross-tenant isolation (row 12) is truly proven — a fake can't run the
   `where id = $1 and user_id = $2` that a leak would bypass.

Every test names the AC row it covers.
"""

import os
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from app.auth import get_current_user_id
from app.main import app

USER_ID = uuid.uuid4()
OTHER_USER_ID = uuid.uuid4()
CHECK_IN_ID = uuid.uuid4()
SECOND_CHECK_IN_ID = uuid.uuid4()
CREATED_AT = datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)


def _check_in_row(**overrides: Any) -> dict[str, Any]:
    """A check_ins row as asyncpg would return it (dict-like), server-owned defaults."""
    row: dict[str, Any] = {
        "id": CHECK_IN_ID,
        "raw_text": "did 5x5 squats at 225",
        "source": "text",
        "entry_date": date(2026, 7, 15),
        "created_at": CREATED_AT,
    }
    row.update(overrides)
    return row


# --- fake pool: two db calls per create/list, so the conn serves primed values IN ORDER ---
#
# The check-in service resolves the caller's timezone first (get_user_timezone -> a scalar,
# expected via fetchval) and then does the real work (insert -> fetchrow returning the row;
# list -> fetch returning rows; delete -> the returned id or None). Every fetch-family method
# pops the next primed value and records (query, args), so pool.conn.calls preserves order.


class _FakeConn:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _next(self) -> Any:
        return self._responses.pop(0) if self._responses else None

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.calls.append((query, args))
        return self._next()

    async def fetchrow(self, query: str, *args: Any) -> Any:
        self.calls.append((query, args))
        return self._next()

    async def fetch(self, query: str, *args: Any) -> Any:
        self.calls.append((query, args))
        return self._next()


class FakePool:
    def __init__(self, responses: list[Any]) -> None:
        self.conn = _FakeConn(responses)

    def acquire(self) -> "_FakeAcquire":
        # Same conn across acquires so calls from BOTH db functions accumulate in order.
        return _FakeAcquire(self.conn)


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


def _sign_in(responses: list[Any]) -> FakePool:
    """Wire the app as if USER_ID holds a valid token and the DB serves `responses` in order."""
    from app.deps import get_pool

    pool = FakePool(responses)
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    app.dependency_overrides[get_pool] = lambda: pool
    return pool


# =========================== POST /check-ins ===========================


# AC row 1: valid text + valid token -> 201; raw_text stored VERBATIM, source='text',
# user_id = caller, entry_date = caller's local today; response echoes it. The INSERT must
# name public.check_ins and carry the verified user_id + raw_text + local-today date.
async def test_post_valid_text_creates_check_in(client: AsyncClient) -> None:
    text = "did 5x5 squats at 225"
    # responses: [timezone read, inserted row]. tz "UTC" -> local today is today's UTC date.
    inserted = _check_in_row(raw_text=text, source="text")
    pool = _sign_in(["UTC", inserted])

    resp = await client.post("/check-ins", json={"text": text})

    assert resp.status_code == 201
    body = resp.json()
    assert body["raw_text"] == text  # verbatim
    assert body["source"] == "text"
    assert body["id"] == str(CHECK_IN_ID)

    # Second db call is the INSERT; it must bind user_id from the token, not the payload.
    insert_query, insert_args = pool.conn.calls[1]
    assert "insert into public.check_ins" in insert_query.lower()
    assert "user_id" in insert_query.lower()
    assert USER_ID in insert_args  # bound from UserIdDep (backend rule 3)
    assert text in insert_args  # raw_text stored verbatim
    assert datetime.now(UTC).date() in insert_args  # entry_date = caller's local today


# AC row 2 (+ backend rules 1 & 3): a body carrying someone else's user_id is REJECTED 422 —
# CheckInCreate is extra="forbid", so the field is literally un-sendable. That is the strongest
# possible form of "ignored / never trusted": the client cannot even name a user_id.
async def test_post_with_user_id_in_body_is_422(client: AsyncClient) -> None:
    _sign_in(["UTC", _check_in_row()])

    resp = await client.post(
        "/check-ins",
        json={"text": "sneaky", "user_id": str(OTHER_USER_ID)},
    )

    assert resp.status_code == 422


# AC row 3: empty / whitespace-only text -> 422, nothing stored.
async def test_post_empty_text_is_422(client: AsyncClient) -> None:
    pool = _sign_in(["UTC", _check_in_row()])

    resp = await client.post("/check-ins", json={"text": ""})

    assert resp.status_code == 422
    assert pool.conn.calls == []  # nothing stored


async def test_post_whitespace_only_text_is_422(client: AsyncClient) -> None:
    pool = _sign_in(["UTC", _check_in_row()])

    resp = await client.post("/check-ins", json={"text": "   "})

    assert resp.status_code == 422
    assert pool.conn.calls == []  # nothing stored


# AC row 4: text longer than 4000 chars -> 422.
async def test_post_text_over_4000_chars_is_422(client: AsyncClient) -> None:
    pool = _sign_in(["UTC", _check_in_row()])

    resp = await client.post("/check-ins", json={"text": "x" * 4001})

    assert resp.status_code == 422
    assert pool.conn.calls == []


# AC row 4 (boundary): exactly 4000 chars is accepted -> 201.
async def test_post_text_at_4000_chars_is_accepted(client: AsyncClient) -> None:
    text = "x" * 4000
    _sign_in(["UTC", _check_in_row(raw_text=text)])

    resp = await client.post("/check-ins", json={"text": text})

    assert resp.status_code == 201
    assert resp.json()["raw_text"] == text


# AC row 5: no token -> 401 (no auth override, so the real dependency runs and rejects).
async def test_post_without_token_is_401(client: AsyncClient) -> None:
    resp = await client.post("/check-ins", json={"text": "no auth"})

    assert resp.status_code == 401


# =========================== GET /check-ins ===========================


# AC row 7: returns today's check-ins, newest first. The DB does the ORDER BY; the service
# must preserve that order. The list statement must scope to BOTH user_id and today's date.
async def test_get_returns_todays_check_ins_in_order(client: AsyncClient) -> None:
    newest = _check_in_row(id=CHECK_IN_ID, raw_text="newest", created_at=CREATED_AT)
    older = _check_in_row(
        id=SECOND_CHECK_IN_ID,
        raw_text="older",
        created_at=CREATED_AT - timedelta(hours=2),
    )
    # responses: [timezone read, list rows already ordered newest-first by the db]
    pool = _sign_in(["UTC", [newest, older]])

    resp = await client.get("/check-ins")

    assert resp.status_code == 200
    body = resp.json()
    assert [r["id"] for r in body] == [str(CHECK_IN_ID), str(SECOND_CHECK_IN_ID)]  # order kept

    list_query, list_args = pool.conn.calls[1]
    q = list_query.lower()
    assert "where user_id = $1 and entry_date = $2" in q  # scoped to owner AND date
    assert "order by created_at desc" in q  # newest first
    assert list_args == (USER_ID, datetime.now(UTC).date())


# AC row 8: no check-ins today -> 200 and an EMPTY LIST (not 404).
async def test_get_with_no_check_ins_today_is_empty_list(client: AsyncClient) -> None:
    _sign_in(["UTC", []])

    resp = await client.get("/check-ins")

    assert resp.status_code == 200
    assert resp.json() == []


# AC row 10: no token -> 401.
async def test_get_without_token_is_401(client: AsyncClient) -> None:
    resp = await client.get("/check-ins")

    assert resp.status_code == 401


# =========================== DELETE /check-ins/{id} ===========================


# AC row 11: delete own check-in -> 204, empty body. The delete statement must filter on
# BOTH id and user_id in the SAME statement (backend rule 2 — no TOCTOU SELECT).
async def test_delete_own_check_in_is_204(client: AsyncClient) -> None:
    pool = _sign_in([CHECK_IN_ID])  # db returns the deleted id -> True

    resp = await client.delete(f"/check-ins/{CHECK_IN_ID}")

    assert resp.status_code == 204
    assert resp.content == b""  # 204 carries no body

    del_query, del_args = pool.conn.calls[0]
    assert "delete from public.check_ins where id = $1 and user_id = $2" in del_query.lower()
    assert del_args == (CHECK_IN_ID, USER_ID)


# AC row 13: delete a nonexistent/own-but-missing row -> db returns nothing -> 404.
# The 404 must come from the delete statement matching no row (id, user_id) — NOT from the
# route being absent. Asserting the db call ran distinguishes "row missing" from "route
# missing", so this test is red against an empty implementation instead of a false green.
async def test_delete_missing_row_is_404(client: AsyncClient) -> None:
    missing_id = uuid.uuid4()
    pool = _sign_in([None])  # nothing returned -> False -> 404

    resp = await client.delete(f"/check-ins/{missing_id}")

    assert resp.status_code == 404
    assert len(pool.conn.calls) == 1  # the delete ran; the 404 is "no row", not "no route"
    del_query, del_args = pool.conn.calls[0]  # the ownership-scoped delete actually ran
    assert "delete from public.check_ins where id = $1 and user_id = $2" in del_query.lower()
    assert del_args == (missing_id, USER_ID)


# AC row 14: malformed id (not a uuid) -> 422 (path validation), no db call.
async def test_delete_malformed_id_is_422(client: AsyncClient) -> None:
    pool = _sign_in([CHECK_IN_ID])

    resp = await client.delete("/check-ins/not-a-uuid")

    assert resp.status_code == 422
    assert pool.conn.calls == []  # never reached the db


# AC row 15: no token -> 401.
async def test_delete_without_token_is_401(client: AsyncClient) -> None:
    resp = await client.delete(f"/check-ins/{CHECK_IN_ID}")

    assert resp.status_code == 401


# =========================== real-DB integration (rows 12, 9, 7) ===========================
#
# Gated exactly like test_health.py: skipped unless a real DATABASE_URL was provided. These
# run against LOCAL Supabase and are the ONLY tests that execute the ownership SQL, so they
# are the only real proof of cross-tenant isolation (row 12). Users A and B are seeded in
# auth.users and torn down in `finally` (cascade removes their check_ins).

requires_db = pytest.mark.skipif(
    not os.getenv("HAS_REAL_DB"),
    reason="no real DATABASE_URL set; real-database integration test skipped",
)


async def _seed_users(pool: Any, *user_ids: uuid.UUID) -> None:
    """Insert minimal auth.users rows; fail loudly (not silently) if the schema needs more."""
    try:
        async with pool.acquire() as conn:
            for uid in user_ids:
                await conn.execute(
                    "insert into auth.users (id, email) values ($1, $2) on conflict do nothing",
                    uid,
                    f"{uid}@test.example",
                )
    except Exception as exc:  # noqa: BLE001 - surface the real seed error to the report
        pytest.fail(f"failed to seed auth.users (adjust seed at /ship if columns changed): {exc}")


async def _delete_users(pool: Any, *user_ids: uuid.UUID) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "delete from auth.users where id = any($1::uuid[])",
            list(user_ids),
        )


# AC row 12 (MANDATORY cross-tenant): user B's token + user A's check_in_id -> the delete
# affects NOTHING (returns False) and A's row is UNCHANGED. 404 not 403, so we never confirm
# the row exists. This is the leak that `where id=$1 and user_id=$2` prevents.
@requires_db
async def test_cross_tenant_delete_leaves_a_row_unchanged() -> None:
    from app.db.check_ins import delete_check_in
    from app.db.pool import close_pool, create_pool
    from app.schemas.check_ins import CheckInCreate
    from app.services.check_ins import create_check_in, list_check_ins

    a, b = uuid.uuid4(), uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a, b)

        created = await create_check_in(pool, a, CheckInCreate(text="A's private note"))
        a_id = created.id

        # B tries to delete A's row: no row matches (id, B) -> False, and A keeps the row.
        assert await delete_check_in(pool, b, a_id) is False
        a_rows = await list_check_ins(pool, a)
        assert any(r.id == a_id for r in a_rows), "A's row must be untouched by B's delete"

        # A deletes its own row: the same statement now matches -> True, and it's gone.
        assert await delete_check_in(pool, a, a_id) is True
        a_rows_after = await list_check_ins(pool, a)
        assert all(r.id != a_id for r in a_rows_after)
    finally:
        await _delete_users(pool, a, b)
        await close_pool(pool)


# AC row 9: user B calls list while user A has rows -> B sees only B's, A sees only A's.
@requires_db
async def test_list_is_isolated_per_user() -> None:
    from app.db.pool import close_pool, create_pool
    from app.schemas.check_ins import CheckInCreate
    from app.services.check_ins import create_check_in, list_check_ins

    a, b = uuid.uuid4(), uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a, b)

        a_row = await create_check_in(pool, a, CheckInCreate(text="A note"))
        b_row = await create_check_in(pool, b, CheckInCreate(text="B note"))

        a_ids = [r.id for r in await list_check_ins(pool, a)]
        b_ids = [r.id for r in await list_check_ins(pool, b)]

        assert a_row.id in a_ids and b_row.id not in a_ids
        assert b_row.id in b_ids and a_row.id not in b_ids
    finally:
        await _delete_users(pool, a, b)
        await close_pool(pool)


# AC row 7 (date scoping, end-to-end): a row dated today and one dated yesterday -> list
# (scoped to local today) returns only the today row.
@requires_db
async def test_list_returns_only_todays_rows() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool
    from app.services.check_ins import list_check_ins

    a = uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a)

        today = datetime.now(UTC).date()  # A has no timezone -> local today is UTC today
        yesterday = today - timedelta(days=1)
        row_today = await insert_check_in(pool, a, "today text", today)
        row_yesterday = await insert_check_in(pool, a, "yesterday text", yesterday)

        ids = [r.id for r in await list_check_ins(pool, a)]
        assert row_today["id"] in ids
        assert row_yesterday["id"] not in ids
    finally:
        await _delete_users(pool, a)
        await close_pool(pool)
