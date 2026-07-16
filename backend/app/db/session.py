"""Per-transaction user identity — the database-enforced *second* lock (issue #24).

The pool connects as a dedicated **non-BYPASSRLS** login role. `authed_conn` stamps the
caller's JWT-verified identity onto a transaction so the owner-only RLS policies that already
exist on every table actually engage on the server path. The payoff is a changed failure
mode: a query that forgets its `user_id` filter now returns **zero rows** (the RLS policy
matches nothing) instead of every user's rows.

This does **not** replace the explicit `where user_id = $2` filters in `db/` — those stay, as
the first lock (see `.claude/rules/backend.md`). Belt and braces: the filter is the primary
boundary; RLS is the net that turns a forgotten filter into an obvious empty result instead of
a silent leak.

Why it's safe on a *pooled* connection: `set_config(..., is_local => true)` and `SET LOCAL`
are **transaction-scoped** — Postgres discards them at commit or rollback, so no identity
survives to the next borrower of the physical connection. The transaction therefore has to be
open *before* anything is set; a `SET LOCAL` outside a transaction is a silent no-op that would
leak. `db/health.py` deliberately does not use this (its `SELECT 1` touches no table and needs
no identity).
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import asyncpg


@asynccontextmanager
async def authed_conn(pool: asyncpg.Pool, user_id: UUID) -> AsyncIterator[asyncpg.Connection]:
    """Acquire a pooled connection and yield it with `user_id`'s identity set for one
    transaction. Use exactly where a db function would otherwise `pool.acquire()`.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. Bind the caller's claims as `request.jwt.claims` for this transaction only.
            #    The full PostgREST/Supabase-shaped payload {"sub", "role"} means auth.uid(),
            #    auth.jwt() and auth.role() all resolve, so today's policies and any future
            #    ones work unchanged. $1 is a bind parameter — never string-interpolated.
            await conn.execute(
                "select set_config('request.jwt.claims', $1, true)",
                json.dumps({"sub": str(user_id), "role": "authenticated"}),
            )
            # 2. Drop from the powerful login role to `authenticated`, the role the RLS
            #    policies are written against. A plain constant string — `SET` can't take a
            #    bind parameter, so the only safe form is one with no user input in it.
            await conn.execute("set local role authenticated")
            yield conn
