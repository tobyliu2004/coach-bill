"""Profile queries. Every statement filters on the verified user id (the first lock) AND
runs inside `authed_conn`, which sets the caller's identity so the owner-only RLS policy
engages as a second, database-enforced lock (see app/db/session.py)."""

from uuid import UUID

import asyncpg

from app.db.session import authed_conn

_COLUMNS = "id, display_name, weight_unit, goal, timezone, consented_at, created_at"


async def get_profile(pool: asyncpg.Pool, user_id: UUID) -> asyncpg.Record | None:
    """The caller's profile row, or None if it doesn't exist."""
    async with authed_conn(pool, user_id) as conn:
        row: asyncpg.Record | None = await conn.fetchrow(
            f"select {_COLUMNS} from public.profiles where id = $1",
            user_id,
        )
        return row


async def get_user_timezone(pool: asyncpg.Pool, user_id: UUID) -> str | None:
    """The caller's IANA timezone, or None (missing row or NULL tz). Callers fall back
    to UTC — see app/time.local_today. Scoped to the verified id like every read here."""
    async with authed_conn(pool, user_id) as conn:
        tz: str | None = await conn.fetchval(
            "select timezone from public.profiles where id = $1",
            user_id,
        )
        return tz


async def get_user_weight_unit(pool: asyncpg.Pool, user_id: UUID) -> str | None:
    """The caller's preferred weight unit ('lb' or 'kg'), or None (missing row).

    The unit lives on the PROFILE, never in the check-in text — "bench 135" says nothing
    about units. Reading it from anywhere else is invisible until someone's chart is off
    by 2.2x. Callers fall back to the column's own default, 'lb'.
    """
    async with authed_conn(pool, user_id) as conn:
        unit: str | None = await conn.fetchval(
            "select weight_unit from public.profiles where id = $1",
            user_id,
        )
        return unit


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
    async with authed_conn(pool, user_id) as conn:
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
