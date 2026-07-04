"""Lowest-level DB access for health checks. Only db/ touches the database."""

import asyncpg


async def ping(pool: asyncpg.Pool) -> None:
    """Run the cheapest possible query to prove the database answers. Raises on failure."""
    async with pool.acquire() as conn:
        await conn.execute("SELECT 1")
