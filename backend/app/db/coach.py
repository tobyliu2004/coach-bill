"""Coach-message queries — the `coach_messages` table, and nothing else.

Every statement filters on the verified user id in the SAME statement (the first lock) and
runs inside `authed_conn`, so the owner-only RLS policy engages as a second, database-
enforced lock. Someone else's row simply matches nothing: a 404, never a 403.

⚠️ WHY THERE IS NO JOIN IN THIS FILE, AND WHY THAT IS THE INTERESTING PART.

`db/trends.py` has to join `check_ins` to learn what day a fact belongs to, which means
every statement there has two user-owned tables in it and needs the owner filter on BOTH —
a rule the repo-wide tripwire (`tests/test_data_isolation.py`) cannot enforce, because it
only checks the word `user_id` appears *somewhere* in the statement. A join fenced on one
side passes it silently. That is a live weakness, filed but unfixed.

`coach_messages` carries its own `user_id`, so this module sidesteps that entire class by
construction: every statement below is single-table and fenced on the owner column. Not an
accident — it is why the reply is stored with its own owner rather than inferred through
its parent check-in. Keep it that way. The moment a query here grows a join, it inherits
the weakness, and `tests/test_coach.py::test_row23_every_coach_statement_is_owner_scoped`
(which asserts a CONTIGUOUS `user_id = $N` bound to the actual caller) becomes the only
thing standing between us and a leak.

Two conventions carried over from db/trends.py, load-bearing rather than stylistic:
  * **Adjacent string literals, never a runtime `+` or `.join()`** — the tripwire parses
    this file's AST, and Python merges adjacent literals into one constant while a runtime
    concat is analysed as fragments that each look like an unguarded statement.
  * **Always qualify `public.`** — an unqualified table name is invisible to the tripwire.
"""

from uuid import UUID

import asyncpg

from app.db.session import authed_conn

# What the API returns for a reply (schemas/coach.py CoachReplyOut). `role` and `user_id`
# are deliberately absent: the caller already knows both — the role is always 'assistant'
# (see below) and the owner is the token's.
_COLUMNS = "id, content, created_at"


async def get_reply_for_check_in(
    pool: asyncpg.Pool, user_id: UUID, check_in_id: UUID
) -> asyncpg.Record | None:
    """The caller's stored reply for one check-in, or None if Bill hasn't answered it yet.

    This read is what makes the endpoint idempotent (AC row 2): a second request returns
    the same row instead of spending Haiku and Sonnet again and storing a second reply. It
    runs BEFORE either model, so a double-click or a component remount costs one database
    round trip rather than ~1.7¢.

    `check_in_id` came from the client and is a claim, not a fact, so it is filtered
    alongside `user_id` in the same statement — someone else's check-in matches nothing
    here, which is what makes the endpoint a 404 rather than a 403 (backend rule 5).

    `limit 1` with a deterministic order rather than bare: the schema does not enforce one
    reply per check-in (no unique constraint), and if a future path ever writes a second
    one, "whichever the planner returned first" is not an answer. Oldest wins, so the reply
    a user has already read stays the reply they see.
    """
    async with authed_conn(pool, user_id) as conn:
        row: asyncpg.Record | None = await conn.fetchrow(
            f"select {_COLUMNS} from public.coach_messages "
            f"where check_in_id = $2 and user_id = $1 and role = 'assistant' "
            f"order by created_at, id "
            f"limit 1",
            user_id,
            check_in_id,
        )
        return row


async def insert_reply(
    pool: asyncpg.Pool, user_id: UUID, check_in_id: UUID, content: str
) -> asyncpg.Record | None:
    """Store Bill's reply; None if the parent check-in isn't the caller's (or is gone).

    THE `where exists` GUARD IS THE REAL LOCK (backend rule 4), not the read that ran
    before it. A check-in can be deleted between the two — AC row 7 is exactly that race —
    and a prior SELECT would be a TOCTOU check that proves nothing by the time the write
    lands. Proving the parent inside the write closes it: no matching parent, no row, and
    RETURNING hands back nothing, which the service turns into a 404 with no orphan.

    Unlike the fact tables in db/facts.py, RLS *would* also catch a mis-owned row here (the
    reply carries `user_id = $1`, so a row hung off someone else's check-in still has to
    satisfy `auth.uid() = user_id`). The guard is not redundant even so: RLS would stop B
    writing a row owned by A, but only this guard stops B attaching a row *they own* to A's
    check-in — which would leak the existence of A's check-in and corrupt A's thread.

    `user_id` is bound from `UserIdDep` and `role` is the server-stamped literal
    'assistant'; neither is ever taken from a payload (backend rule 3).

    `on conflict do nothing` covers the race the guard cannot: two CONCURRENT requests both
    read "no reply yet" and both reach this insert. The partial unique index added in
    20260802170638 makes the loser's write a no-op instead of a duplicate row — but note
    that RETURNING then yields nothing for BOTH the blocked-guard case and the lost-race
    case, which look identical from here. Disambiguating them is the caller's job (see
    `services/coach.py`, which re-reads): a blocked guard is a 404, a lost race is the
    winner's reply. No conflict target is named on purpose — `do nothing` catches any unique
    violation, so this keeps working if the index's predicate is ever narrowed.
    """
    async with authed_conn(pool, user_id) as conn:
        row: asyncpg.Record | None = await conn.fetchrow(
            "insert into public.coach_messages (user_id, check_in_id, role, content) "
            "select $1, $2, 'assistant', $3 "
            " where exists "
            "       (select 1 from public.check_ins where id = $2 and user_id = $1) "
            "on conflict do nothing "
            f"returning {_COLUMNS}",
            user_id,
            check_in_id,
            content,
        )
        return row


# ⚠️ NOTHING IN THIS MODULE DELETES A ROW, ON PURPOSE (#48). A `delete_reply_with_content`
# used to live here, matching on the reply's exact text so that a `CRISIS_REPLY` could never
# be the row it removed. It served one caller — the retract endpoint — and that endpoint
# existed only to escape a wrong `off_topic` verdict, so both died with the label.
#
# The safety rule survives them and is now enforced by absence: no statement here can delete
# a coach message, `authenticated` no longer holds the `delete` grant, and there is no route
# that would call one. A crisis reply is un-re-rollable because there is no mechanism, not
# because a string comparison came out right. `tests/test_coach.py::test_i48_row25_*` asserts
# the statement does not exist anywhere in `app/`, which is what keeps this from quietly
# coming back. The argument in full is in `services/coach.py`'s module docstring.


async def list_replies_for_check_ins(
    pool: asyncpg.Pool, user_id: UUID, check_in_ids: list[UUID]
) -> list[asyncpg.Record]:
    """Every reply the caller owns on the given check-ins, oldest first.

    ONE query over ALL the ids (`= any($2)`) rather than one per check-in: `GET /check-ins`
    bundles replies (AC row 30), so a per-row query would be an N+1 that grows with the
    user's history — the same shape `list_facts_for_check_ins` exists to avoid.

    Scoped by `user_id` as well as the ids. The ids came from a prior read of the caller's
    own check-ins, but a read is not a permission and this is a fresh leak surface; the
    filter costs nothing and is the difference between a guarantee and an assumption.

    Serves two callers with different needs, which is why it returns rows rather than a
    map: the list endpoint indexes them by `check_in_id`, and the coach service takes the
    newest few for continuity (AC row 27). Oldest-first here because that is the order a
    conversation reads in; whoever wants the newest slices the tail.
    """
    if not check_in_ids:
        return []

    async with authed_conn(pool, user_id) as conn:
        rows: list[asyncpg.Record] = await conn.fetch(
            f"select {_COLUMNS}, check_in_id from public.coach_messages "
            f"where check_in_id = any($2) and user_id = $1 and role = 'assistant' "
            f"order by created_at, id",
            user_id,
            check_in_ids,
        )
        return rows
