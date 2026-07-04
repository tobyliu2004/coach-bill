"""Connection-pool lifecycle. The only place we build/tear down the asyncpg pool."""

import asyncpg


async def create_pool(dsn: str) -> asyncpg.Pool:
    """Create the connection pool. Called once, at app startup.

    min_size=0 is deliberate: no connections are opened eagerly, so the app boots even
    when the database is unreachable and GET /health/db can *report* the outage (503)
    instead of the whole app crash-looping. Connections open lazily on first use and
    are then kept in the pool.
    """
    return await asyncpg.create_pool(dsn=dsn, min_size=0, max_size=10)


async def close_pool(pool: asyncpg.Pool) -> None:
    """Close the pool and all its connections. Called once, at app shutdown."""
    await pool.close()
