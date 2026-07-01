"""Business logic for health checks: time the db ping and shape the result."""

import time

import asyncpg

from app.db.health import ping
from app.schemas.health import HealthStatus


async def check_db(pool: asyncpg.Pool) -> HealthStatus:
    """Ping the database and return an 'up' status with the round-trip latency.

    Raises if the database is unreachable — the route layer turns that into a 503.
    """
    start = time.perf_counter()
    await ping(pool)
    latency_ms = (time.perf_counter() - start) * 1000
    return HealthStatus(ok=True, db="up", latency_ms=round(latency_ms, 2))
