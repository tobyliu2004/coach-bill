"""Check-in queries. Every statement filters on the verified user id in the SAME
statement (the first lock) AND runs inside `authed_conn`, which stamps the caller's identity
so the owner-only RLS policy engages as a second, database-enforced lock — a forgotten filter
returns zero rows, not everyone's (see app/db/session.py and .claude/rules/backend.md).
Someone else's row simply matches nothing: a 404, never a 403.
"""

from datetime import date
from uuid import UUID

import asyncpg

from app.db.session import authed_conn

_COLUMNS = "id, raw_text, source, entry_date, created_at, extraction_status"


async def insert_check_in(
    pool: asyncpg.Pool, user_id: UUID, raw_text: str, entry_date: date
) -> asyncpg.Record:
    """Insert a check-in owned by `user_id`; `source` is server-stamped `'text'`.

    An INSERT has no WHERE, so binding `user_id` from `UserIdDep` here (never the
    payload) is the only thing tying the row to its owner (backend rule 3).
    """
    async with authed_conn(pool, user_id) as conn:
        row: asyncpg.Record | None = await conn.fetchrow(
            f"insert into public.check_ins (user_id, raw_text, source, entry_date) "
            f"values ($1, $2, 'text', $3) returning {_COLUMNS}",
            user_id,
            raw_text,
            entry_date,
        )
        # RETURNING on a successful INSERT always yields exactly one row.
        assert row is not None
        return row


async def list_check_ins_in_range(
    pool: asyncpg.Pool, user_id: UUID, start: date, end: date
) -> list[asyncpg.Record]:
    """The caller's check-ins between two local dates, newest day first.

    `between` is INCLUSIVE at both ends, which is the whole semantic of the window the
    service computes: "7 days" is today plus the six before it, and a row dated exactly
    `start` is inside it. Today alone is `start == end` — one query serves both the daily
    screen and history, so they can never drift apart on what a day means.

    Two sort keys, not one: `created_at` alone would interleave days whenever a check-in was
    back-dated or logged either side of the user's midnight, and the history screen groups by
    `entry_date`. Ordering by the grouping key first is what makes each day one contiguous
    block. Covered by `check_ins_user_date_idx` on `(user_id, entry_date)`.
    """
    async with authed_conn(pool, user_id) as conn:
        rows: list[asyncpg.Record] = await conn.fetch(
            f"select {_COLUMNS} from public.check_ins "
            f"where user_id = $1 and entry_date between $2 and $3 "
            f"order by entry_date desc, created_at desc",
            user_id,
            start,
            end,
        )
        return rows


async def set_extraction_status(
    pool: asyncpg.Pool, user_id: UUID, check_in_id: UUID, status: str
) -> None:
    """Stamp the outcome of extraction on the caller's check-in.

    A separate statement from the INSERT because extraction happens *between* them: the row
    is saved as 'pending' first so a crash or a dead vendor still leaves the raw text on
    disk (AC row 9). `id` AND `user_id` in the same statement, as with every targeted write.

    No RETURNING: the caller already knows the status it just asked for, so reading the row
    back would be a round trip that can only tell it what it told us.
    """
    async with authed_conn(pool, user_id) as conn:
        await conn.execute(
            "update public.check_ins set extraction_status = $1 where id = $2 and user_id = $3",
            status,
            check_in_id,
            user_id,
        )


async def delete_check_in(pool: asyncpg.Pool, user_id: UUID, check_in_id: UUID) -> bool:
    """Delete the caller's check-in; True iff a row was actually deleted.

    `id` AND `user_id` in the same statement (backend rule 2) — a prior SELECT would be a
    TOCTOU race, and someone else's id matches no row, so the caller learns nothing.
    """
    async with authed_conn(pool, user_id) as conn:
        deleted_id: UUID | None = await conn.fetchval(
            "delete from public.check_ins where id = $1 and user_id = $2 returning id",
            check_in_id,
            user_id,
        )
        return deleted_id is not None
