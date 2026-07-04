"""Behavior of GET /health/db, written before the implementation.

Unit tests substitute a fake connection pool for the real one, so they exercise the full
route → service → db path deterministically with no database. The integration test hits a
real database and is skipped unless a real DATABASE_URL was provided.
"""

import os

import pytest
from httpx import AsyncClient

from app.main import app
from app.routes.health import get_pool

# --- a minimal fake asyncpg pool: `async with pool.acquire() as conn: await conn.execute(...)`


class _FakeConn:
    def __init__(self, *, fail: bool) -> None:
        self._fail = fail

    async def execute(self, query: str) -> str:
        if self._fail:
            raise RuntimeError("simulated database failure")
        return "SELECT 1"


class _FakeAcquire:
    def __init__(self, *, fail: bool) -> None:
        self._fail = fail

    async def __aenter__(self) -> _FakeConn:
        return _FakeConn(fail=self._fail)

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class FakePool:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(fail=self._fail)


async def test_health_db_reports_up(client: AsyncClient) -> None:
    app.dependency_overrides[get_pool] = lambda: FakePool(fail=False)

    resp = await client.get("/health/db")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["db"] == "up"
    assert body["latency_ms"] >= 0


async def test_health_db_reports_down_on_failure(client: AsyncClient) -> None:
    app.dependency_overrides[get_pool] = lambda: FakePool(fail=True)

    resp = await client.get("/health/db")

    assert resp.status_code == 503
    body = resp.json()
    assert body["ok"] is False
    assert body["db"] == "down"


@pytest.mark.skipif(
    not os.getenv("HAS_REAL_DB"),
    reason="no real DATABASE_URL set; real-database integration test skipped",
)
async def test_check_db_against_real_database() -> None:
    from app.db.pool import close_pool, create_pool
    from app.services.health import check_db

    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        result = await check_db(pool)
    finally:
        await close_pool(pool)

    assert result.ok is True
    assert result.db == "up"
    assert result.latency_ms >= 0
