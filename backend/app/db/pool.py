"""Connection-pool lifecycle. The only place we build/tear down the asyncpg pool."""

import asyncpg


async def create_pool(dsn: str) -> asyncpg.Pool:
    """Open a connection pool to Postgres. Called once, at app startup."""
    return await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=10)


async def close_pool(pool: asyncpg.Pool) -> None:
    """Close the pool and all its connections. Called once, at app shutdown."""
    await pool.close()
