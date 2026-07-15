"""Check-in business logic: resolve the caller's local day, map db rows to API shapes.
Routes call this, never db/. Services return None/False for a missing row; the route
decides that means 404.
"""

from uuid import UUID

import asyncpg

from app.db import check_ins as check_ins_db
from app.db.profiles import get_user_timezone
from app.schemas.check_ins import CheckInCreate, CheckInOut
from app.time import local_today


async def create_check_in(pool: asyncpg.Pool, user_id: UUID, body: CheckInCreate) -> CheckInOut:
    """Store a check-in under the caller's local today (source is server-stamped 'text').

    Two db reads/writes: resolve the caller's tz, then insert against that local date, so
    the write agrees with what GET will later filter on.
    """
    tz = await get_user_timezone(pool, user_id)
    row = await check_ins_db.insert_check_in(pool, user_id, body.text, local_today(tz))
    return CheckInOut(**dict(row))


async def list_check_ins(pool: asyncpg.Pool, user_id: UUID) -> list[CheckInOut]:
    """The caller's check-ins for their local today, newest first."""
    tz = await get_user_timezone(pool, user_id)
    rows = await check_ins_db.list_check_ins_for_date(pool, user_id, local_today(tz))
    return [CheckInOut(**dict(row)) for row in rows]


async def delete_check_in(pool: asyncpg.Pool, user_id: UUID, check_in_id: UUID) -> bool:
    """Delete the caller's check-in; False if no such row (route turns that into 404)."""
    return await check_ins_db.delete_check_in(pool, user_id, check_in_id)
