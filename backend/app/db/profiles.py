"""Profile queries. Every statement filters on the verified user id — the pooler role
bypasses RLS, so this filter is the security boundary (see app/auth.py)."""

from uuid import UUID

import asyncpg

_COLUMNS = "id, display_name, weight_unit, goal, timezone, consented_at, created_at"


async def get_profile(pool: asyncpg.Pool, user_id: UUID) -> asyncpg.Record | None:
    """The caller's profile row, or None if it doesn't exist."""
    async with pool.acquire() as conn:
        row: asyncpg.Record | None = await conn.fetchrow(
            f"select {_COLUMNS} from public.profiles where id = $1",
            user_id,
        )
        return row


async def update_profile(
    pool: asyncpg.Pool,
    user_id: UUID,
    *,
    display_name: str | None,
    weight_unit: str | None,
    goal: str | None,
    timezone: str | None,
    set_consent: bool,
) -> asyncpg.Record | None:
    """Patch-update the caller's profile; None args leave their column unchanged.

    consented_at only ever moves from null to now() — consent is stamped once, never
    re-stamped or cleared by later updates.
    """
    async with pool.acquire() as conn:
        row: asyncpg.Record | None = await conn.fetchrow(
            f"""
            update public.profiles
               set display_name = coalesce($2, display_name),
                   weight_unit  = coalesce($3, weight_unit),
                   goal         = coalesce($4, goal),
                   timezone     = coalesce($5, timezone),
                   consented_at = case when $6 then coalesce(consented_at, now())
                                       else consented_at end
             where id = $1
            returning {_COLUMNS}
            """,
            user_id,
            display_name,
            weight_unit,
            goal,
            timezone,
            set_consent,
        )
        return row
