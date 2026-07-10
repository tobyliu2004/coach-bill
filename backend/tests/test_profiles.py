"""Behavior of GET/PATCH /me, written before the implementation.

Auth is faked by overriding the verified-user dependency (the JWT machinery itself is
covered in test_auth.py); the database is faked at the pool boundary like test_health.py.
The fakes can't execute SQL, so what these tests prove is the contract: status codes,
validation, response shape, and that the db layer is invoked with the right arguments.
The SQL itself is three static statements reviewed by eye (and by the real-DB check-in
flows once those exist).
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from httpx import AsyncClient

from app.auth import get_current_user_id
from app.main import app

USER_ID = uuid.uuid4()
CREATED_AT = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)


def _profile_row(**overrides: Any) -> dict[str, Any]:
    """A profiles row as asyncpg would return it (dict-like), fresh-signup defaults."""
    row: dict[str, Any] = {
        "id": USER_ID,
        "display_name": None,
        "weight_unit": "lb",
        "goal": None,
        "timezone": None,
        "consented_at": None,
        "created_at": CREATED_AT,
    }
    row.update(overrides)
    return row


# --- fake pool that records fetchrow calls: `async with pool.acquire() as conn` ---


class _FakeConn:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.calls.append((query, args))
        return self._row


class FakePool:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.conn = _FakeConn(row)

    def acquire(self) -> "_FakeAcquire":
        return _FakeAcquire(self.conn)


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


def _sign_in(row: dict[str, Any] | None) -> FakePool:
    """Wire the app as if USER_ID holds a valid token and the DB would return `row`."""
    from app.deps import get_pool

    pool = FakePool(row)
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    app.dependency_overrides[get_pool] = lambda: pool
    return pool


# --- GET /me ---


async def test_get_me_without_token_is_401(client: AsyncClient) -> None:
    resp = await client.get("/me")

    assert resp.status_code == 401


async def test_get_me_returns_the_callers_profile(client: AsyncClient) -> None:
    _sign_in(_profile_row())

    resp = await client.get("/me")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(USER_ID)
    assert body["weight_unit"] == "lb"
    assert body["goal"] is None
    assert body["consented_at"] is None


async def test_get_me_queries_by_the_verified_user_id(client: AsyncClient) -> None:
    """The RLS-bypassing role means this filter IS the security boundary."""
    pool = _sign_in(_profile_row())

    await client.get("/me")

    (query, args) = pool.conn.calls[0]
    assert "where id = $1" in query.lower()
    assert args == (USER_ID,)


async def test_get_me_missing_row_is_404(client: AsyncClient) -> None:
    _sign_in(None)

    resp = await client.get("/me")

    assert resp.status_code == 404


# --- PATCH /me ---


async def test_patch_me_without_token_is_401(client: AsyncClient) -> None:
    resp = await client.patch("/me", json={"goal": "get strong"})

    assert resp.status_code == 401


async def test_patch_me_updates_profile_fields(client: AsyncClient) -> None:
    updated = _profile_row(
        display_name="Toby",
        goal="cut to 175",
        weight_unit="kg",
        timezone="America/Los_Angeles",
    )
    pool = _sign_in(updated)

    resp = await client.patch(
        "/me",
        json={
            "display_name": "Toby",
            "goal": "cut to 175",
            "weight_unit": "kg",
            "timezone": "America/Los_Angeles",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "Toby"
    assert body["goal"] == "cut to 175"
    assert body["weight_unit"] == "kg"
    assert body["timezone"] == "America/Los_Angeles"
    # The update ran against the verified user id, with the new values.
    (query, args) = pool.conn.calls[0]
    assert "update public.profiles" in query.lower()
    assert args[0] == USER_ID
    assert "cut to 175" in args


async def test_patch_me_rejects_unknown_weight_unit(client: AsyncClient) -> None:
    _sign_in(_profile_row())

    resp = await client.patch("/me", json={"weight_unit": "stone"})

    assert resp.status_code == 422


async def test_patch_me_rejects_fake_timezone(client: AsyncClient) -> None:
    _sign_in(_profile_row())

    resp = await client.patch("/me", json={"timezone": "Mars/Olympus"})

    assert resp.status_code == 422


async def test_patch_me_rejects_unknown_fields(client: AsyncClient) -> None:
    """Typos in a PATCH body must fail loudly, not silently do nothing."""
    _sign_in(_profile_row())

    resp = await client.patch("/me", json={"gaol": "typo"})

    assert resp.status_code == 422


async def test_patch_me_consent_stamps_consented_at(client: AsyncClient) -> None:
    stamped = _profile_row(consented_at=datetime(2026, 7, 6, 9, 30, tzinfo=UTC))
    pool = _sign_in(stamped)

    resp = await client.patch("/me", json={"consent": True})

    assert resp.status_code == 200
    assert resp.json()["consented_at"] is not None
    # The db layer was told to stamp consent.
    (_query, args) = pool.conn.calls[0]
    assert True in args


async def test_patch_me_missing_row_is_404(client: AsyncClient) -> None:
    _sign_in(None)

    resp = await client.patch("/me", json={"goal": "get strong"})

    assert resp.status_code == 404
