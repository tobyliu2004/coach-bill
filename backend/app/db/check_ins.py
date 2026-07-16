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

_COLUMNS = "id, raw_text, source, entry_date, created_at"


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


async def list_check_ins_for_date(
    pool: asyncpg.Pool, user_id: UUID, entry_date: date
) -> list[asyncpg.Record]:
    """The caller's check-ins for one local day, newest first."""
    async with authed_conn(pool, user_id) as conn:
        rows: list[asyncpg.Record] = await conn.fetch(
            f"select {_COLUMNS} from public.check_ins "
            f"where user_id = $1 and entry_date = $2 order by created_at desc",
            user_id,
            entry_date,
        )
        return rows


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
