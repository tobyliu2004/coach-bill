"""Oracle suite for issue #21 — the real-database tier. Rows 5, 6, 7, 22, 24, 27-31.

Part of commit #1 on `feat/21-coach-replies`, written BEFORE any implementation exists.
Encodes the rows of the approved 42-row table that a fake pool CANNOT prove, because what
they assert is what SQL does:

  - Row 5 is the MANDATORY cross-tenant row (`backend.md`): user B's token + user A's
    check_in_id -> 404, and no `coach_messages` row exists for either user. A fake cannot
    execute the `where ... and user_id = $1` that a leak would bypass, so a fake asserting
    this would only be asserting about itself.
  - Row 6 is "zero model calls on that cross-tenant call" — ownership is checked before we
    spend money, so a stranger cannot bill us by guessing ids.
  - Row 7 is the rule-4 `where exists (... and user_id = $1)` guard actually executing.
  - Rows 22/24/27 are what the assembled context CONTAINS when a real database holds two
    users' data, sixty days of history, and five prior replies.
  - Rows 28/29/31 are the stored row itself, read from OUTSIDE RLS (asserting "A's rows are
    untouched" through A's own policy would ask the mechanism under test to grade itself).
  - Row 30 is a reply surviving a refresh.

Gated exactly like tests/test_rls_identity.py and tests/test_extraction_db.py, and for the
same reason: `.env` is production and connects as the BYPASSRLS `postgres` role, against
which every isolation assertion here would be a false green.
  - RLS_DATABASE_URL       — the app pool as the fail-closed non-BYPASSRLS role
                             (locally `coach_app`); the code under test uses it.
  - RLS_ADMIN_DATABASE_URL — a privileged connection used ONLY by fixtures, to seed
                             auth.users and to READ coach_messages past RLS.
Skipped unless RLS_DATABASE_URL is set; if it is set and the admin DSN is not, these fail
loudly rather than pretending to pass.

Expect these to be red twice over at oracle time: `app.services.coach` does not exist, and
`coach_messages` currently carries NO grants for `authenticated` (see
tests/test_table_privileges.py's `C3_APP_VERBS`), so every statement here would be
"permission denied" until row 32's migration lands. Both are the correct failure.

Every test names the AC row it covers.
"""

import os
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg
import pytest
from httpx import AsyncClient

from app.auth import get_current_user_id
from app.main import app

requires_rls_db = pytest.mark.skipif(
    not os.getenv("RLS_DATABASE_URL"),
    reason="RLS_DATABASE_URL not set; coach real-DB suite skipped",
)

COACH_TEXT = "Solid session. Keep the bar path over midfoot next time."


# =====================================================================================
# Fixtures — privileged, never the code under test
# =====================================================================================


def _require_admin_dsn() -> str:
    dsn = os.getenv("RLS_ADMIN_DATABASE_URL")
    if not dsn:
        pytest.fail(
            "RLS_DATABASE_URL is set but RLS_ADMIN_DATABASE_URL is not; this suite needs a "
            "privileged connection to seed auth.users and to read coach_messages past RLS "
            "when asserting another user's rows do not exist."
        )
    return dsn


async def _admin_seed_users(admin_dsn: str, *user_ids: uuid.UUID) -> None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        for uid in user_ids:
            await conn.execute(
                "insert into auth.users (id, email) values ($1, $2) on conflict do nothing",
                uid,
                f"{uid}@coach.test",
            )
    finally:
        await conn.close()


async def _admin_delete_users(admin_dsn: str, *user_ids: uuid.UUID) -> None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute("delete from auth.users where id = any($1::uuid[])", list(user_ids))
    finally:
        await conn.close()


async def _admin_replies(admin_dsn: str, user_id: uuid.UUID) -> list[asyncpg.Record]:
    """Every coach_messages row a user owns, read from OUTSIDE RLS."""
    conn = await asyncpg.connect(admin_dsn)
    try:
        rows: list[asyncpg.Record] = await conn.fetch(
            "select id, user_id, role, content, check_in_id, created_at "
            "from public.coach_messages where user_id = $1 order by created_at",
            user_id,
        )
        return rows
    finally:
        await conn.close()


async def _admin_seed_reply(
    admin_dsn: str, user_id: uuid.UUID, check_in_id: uuid.UUID, content: str
) -> uuid.UUID:
    """Seed a stored assistant reply (a FIXTURE — the app's own insert is what row 28 tests)."""
    conn = await asyncpg.connect(admin_dsn)
    try:
        new_id: uuid.UUID = await conn.fetchval(
            "insert into public.coach_messages (user_id, check_in_id, role, content) "
            "values ($1, $2, 'assistant', $3) returning id",
            user_id,
            check_in_id,
            content,
        )
        return new_id
    finally:
        await conn.close()


async def _admin_check_in(admin_dsn: str, check_in_id: uuid.UUID) -> asyncpg.Record | None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        row: asyncpg.Record | None = await conn.fetchrow(
            "select id, user_id, raw_text, entry_date from public.check_ins where id = $1",
            check_in_id,
        )
        return row
    finally:
        await conn.close()


async def _admin_any_exercise_id(admin_dsn: str) -> uuid.UUID:
    conn = await asyncpg.connect(admin_dsn)
    try:
        ex_id: uuid.UUID | None = await conn.fetchval(
            "select id from public.exercises where canonical_id is null order by name limit 1"
        )
        if ex_id is None:
            pytest.fail("the seeded exercise catalog is empty; row 22 needs one movement to log")
        return ex_id
    finally:
        await conn.close()


async def _admin_seed_facts(
    admin_dsn: str,
    user_id: uuid.UUID,
    check_in_id: uuid.UUID,
    *,
    exercise_id: uuid.UUID,
    weight_kg: Decimal,
    sleep_hours: Decimal,
    bodyweight_kg: Decimal,
    calories: Decimal,
) -> None:
    """One row in each fact table, so the assembled context has sets/macros/sleep/bodyweight."""
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(
            "insert into public.workout_sets "
            "(user_id, check_in_id, exercise_id, set_number, reps, weight_kg) "
            "values ($1, $2, $3, 1, 5, $4)",
            user_id,
            check_in_id,
            exercise_id,
            weight_kg,
        )
        await conn.execute(
            "insert into public.sleep_entries (user_id, check_in_id, hours) values ($1, $2, $3)",
            user_id,
            check_in_id,
            sleep_hours,
        )
        await conn.execute(
            "insert into public.bodyweight_entries (user_id, check_in_id, weight_kg) "
            "values ($1, $2, $3)",
            user_id,
            check_in_id,
            bodyweight_kg,
        )
        await conn.execute(
            "insert into public.nutrition_entries "
            "(user_id, check_in_id, description, calories, protein_g, carbs_g, fat_g) "
            "values ($1, $2, 'seeded meal', $3, 10, 10, 10)",
            user_id,
            check_in_id,
            calories,
        )
    finally:
        await conn.close()


async def _seed_check_in(
    pool: asyncpg.Pool, user_id: uuid.UUID, text: str, entry_date: date | None = None
) -> uuid.UUID:
    """Write a check-in under THAT user's own identity (RLS with-check allows it)."""
    from app.db.session import authed_conn

    async with authed_conn(pool, user_id) as conn:
        cid: uuid.UUID = await conn.fetchval(
            "insert into public.check_ins (user_id, raw_text, source, entry_date) "
            "values ($1, $2, 'text', $3) returning id",
            user_id,
            text,
            entry_date if entry_date is not None else datetime.now(UTC).date(),
        )
        return cid


# =====================================================================================
# Recording fakes for the two models — no network, and a call COUNTER (rows 6, 10, 15)
# =====================================================================================


class _RecordingGate:
    def __init__(self, label: str = "coach") -> None:
        self._label = label
        self.calls: list[str] = []

    async def classify(self, text: str) -> Any:
        from app.ai.gate import Intent

        self.calls.append(text)
        return Intent.model_validate({"label": self._label})


class _RecordingCoach:
    def __init__(self, text: str = COACH_TEXT) -> None:
        self._text = text
        self.calls: list[tuple[str, str]] = []

    async def reply(self, context: str, text: str) -> str:
        self.calls.append((context, text))
        return self._text

    @property
    def last_context(self) -> str:
        assert self.calls, "the coach was never called, so there is no context to inspect"
        return self.calls[-1][0]


def _sign_in_as(user_id: uuid.UUID, pool: asyncpg.Pool, gate: Any, coach: Any) -> None:
    """Wire the ASGI app to a REAL pool and a verified identity, with fake models."""
    from app.ai.coach import get_coach
    from app.ai.gate import get_gate
    from app.deps import get_pool

    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[get_gate] = lambda: gate
    app.dependency_overrides[get_coach] = lambda: coach


# =====================================================================================
# A. Ownership (rows 5, 6, 7)
# =====================================================================================


# AC row 5 (MANDATORY cross-tenant): user B's token + user A's check_in_id -> 404, NOT 403 —
# a 403 would confirm A's row exists — and NO coach_messages row exists for either user.
# A's check-in itself is unchanged. Modelled on
# test_rls_identity.py::test_cross_tenant_delete_leaves_a_row_unchanged.
@requires_rls_db
async def test_row5_user_b_cannot_reply_to_user_as_check_in(client: AsyncClient) -> None:
    from app.db.pool import close_pool, create_pool

    admin = _require_admin_dsn()
    a, b = uuid.uuid4(), uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a, b)
        a_id = await _seed_check_in(pool, a, "A's private check-in")

        gate, coach = _RecordingGate(), _RecordingCoach()
        _sign_in_as(b, pool, gate, coach)
        resp = await client.post(f"/check-ins/{a_id}/reply")

        assert resp.status_code == 404  # not 403 — never confirm the row exists
        assert await _admin_replies(admin, b) == []  # B stored nothing
        assert await _admin_replies(admin, a) == []  # ...and nothing landed on A either

        a_row = await _admin_check_in(admin, a_id)
        assert a_row is not None, "A's check-in must still exist"
        assert a_row["raw_text"] == "A's private check-in"  # unchanged
        assert a_row["user_id"] == a
    finally:
        await _admin_delete_users(admin, a, b)
        await close_pool(pool)


# AC row 6: that same cross-tenant call makes ZERO model calls. Ownership is checked BEFORE
# we spend money, so a stranger cannot bill us by guessing ids. A counter of exactly 0 —
# nothing else would distinguish "never called" from "called and discarded".
@requires_rls_db
async def test_row6_cross_tenant_call_spends_no_model_calls(client: AsyncClient) -> None:
    from app.db.pool import close_pool, create_pool

    admin = _require_admin_dsn()
    a, b = uuid.uuid4(), uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a, b)
        a_id = await _seed_check_in(pool, a, "A's private check-in")

        gate, coach = _RecordingGate(), _RecordingCoach()
        _sign_in_as(b, pool, gate, coach)
        resp = await client.post(f"/check-ins/{a_id}/reply")

        assert resp.status_code == 404
        assert len(gate.calls) == 0  # Haiku never ran
        assert len(coach.calls) == 0  # Sonnet never ran
    finally:
        await _admin_delete_users(admin, a, b)
        await close_pool(pool)


# AC row 7: the check-in is gone by the time the insert runs -> the rule-4
# `where exists (... and user_id = $1)` guard matches nothing, `insert_reply` returns None,
# and no orphan row is left behind. The earlier ownership read is convenience; THIS is the
# TOCTOU-proof lock, and only a real database executes it.
@requires_rls_db
async def test_row7_insert_guard_blocks_a_reply_to_a_vanished_check_in() -> None:
    from app.db.coach import insert_reply
    from app.db.pool import close_pool, create_pool

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        a_id = await _seed_check_in(pool, a, "about to be deleted")

        # The owner's own insert works while the parent exists...
        landed = await insert_reply(pool, a, a_id, COACH_TEXT)
        assert landed is not None
        assert landed["content"] == COACH_TEXT

        # ...and stops working the moment the parent is gone (the race row 7 describes).
        gone_id = await _seed_check_in(pool, a, "deleted before the insert")
        from app.db.check_ins import delete_check_in

        assert await delete_check_in(pool, a, gone_id) is True
        assert await insert_reply(pool, a, gone_id, "orphan") is None

        contents = [r["content"] for r in await _admin_replies(admin, a)]
        assert contents == [COACH_TEXT]  # exactly one row; no orphan
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# =====================================================================================
# B. Context assembly against real data (rows 22, 24, 27)
# =====================================================================================


# AC row 22: the context built for user A contains NONE of user B's check-ins, sets, macros,
# sleep or bodyweight. A context window with another user's data in it is a leak that then
# gets fed to an LLM.
#
# A's own marker is asserted PRESENT first: without that, an implementation that built an
# empty context would satisfy every "B is absent" assertion vacuously.
@requires_rls_db
async def test_row22_context_for_a_contains_nothing_of_bs(client: AsyncClient) -> None:
    from app.db.pool import close_pool, create_pool
    from app.services.coach import reply_to_check_in

    admin = _require_admin_dsn()
    a, b = uuid.uuid4(), uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a, b)
        exercise_id = await _admin_any_exercise_id(admin)

        a_id = await _seed_check_in(pool, a, "AMARKER squat session")
        await _admin_seed_facts(
            admin,
            a,
            a_id,
            exercise_id=exercise_id,
            weight_kg=Decimal("101.5"),
            sleep_hours=Decimal("7.25"),
            bodyweight_kg=Decimal("80.5"),
            calories=Decimal("2101"),
        )
        b_id = await _seed_check_in(pool, b, "BMARKER private bench session")
        await _admin_seed_facts(
            admin,
            b,
            b_id,
            exercise_id=exercise_id,
            weight_kg=Decimal("222.5"),
            sleep_hours=Decimal("3.75"),
            bodyweight_kg=Decimal("133.25"),
            calories=Decimal("4321"),
        )
        await _admin_seed_reply(admin, b, b_id, "BREPLYMARKER — B's private coaching reply")

        gate, coach = _RecordingGate(), _RecordingCoach()
        result = await reply_to_check_in(pool, a, a_id, gate, coach)

        assert result is not None
        context = coach.last_context
        # Asserted FIRST and deliberately: if the context were empty, every "B is absent"
        # assertion below would pass vacuously.
        assert "AMARKER" in context, "A's own check-in is missing from A's context"
        for leaked in ("BMARKER", "BREPLYMARKER", "222.5", "3.75", "133.25", "4321", str(b_id)):
            assert leaked not in context, f"user B's data leaked into A's context: {leaked!r}"
    finally:
        await _admin_delete_users(admin, a, b)
        await close_pool(pool)


# AC row 24: a user with 60 days of history gets exactly the last 14 LOCAL days of context;
# day 15 is absent. Truncation is by COUNT, not tokens — deterministic and testable.
#
# "14 days" is INCLUSIVE of today, the window convention issue #20 approved and the one
# `list_check_ins(days=...)` already implements: today plus the 13 before it. So the day 14
# days back is the 15th day and must not appear.
@requires_rls_db
async def test_row24_context_holds_fourteen_days_and_not_the_fifteenth() -> None:
    from app.db.pool import close_pool, create_pool
    from app.services.coach import reply_to_check_in

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        today = datetime.now(UTC).date()  # no profile timezone -> local today is UTC today
        ids: dict[int, uuid.UUID] = {}
        for days_ago in range(60):
            ids[days_ago] = await _seed_check_in(
                pool,
                a,
                f"DAYMARK{days_ago:02d} training note",
                today - timedelta(days=days_ago),
            )

        gate, coach = _RecordingGate(), _RecordingCoach()
        result = await reply_to_check_in(pool, a, ids[0], gate, coach)

        assert result is not None
        context = coach.last_context
        for days_ago in range(14):  # today .. 13 days back == 14 local days
            assert f"DAYMARK{days_ago:02d}" in context, f"day {days_ago} missing from the context"
        for days_ago in (14, 15, 30, 59):  # the 15th day back and beyond
            assert f"DAYMARK{days_ago:02d}" not in context, (
                f"day {days_ago} is outside the 14-day window but is in the context"
            )
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# AC row 27 (behavioural half): the LAST 3 assistant replies ride along — not two, not five —
# so Bill doesn't repeat himself verbatim day to day without becoming a chat interface.
@requires_rls_db
async def test_row27_only_the_three_most_recent_replies_are_in_the_context() -> None:
    from app.db.pool import close_pool, create_pool
    from app.services.coach import reply_to_check_in

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        today = datetime.now(UTC).date()
        # Seeded oldest-first so created_at follows the marker: REPLYMARK05 is five days
        # back (the oldest) and REPLYMARK01 is yesterday (the newest).
        for days_ago in range(5, 0, -1):
            past_id = await _seed_check_in(
                pool, a, f"older check-in {days_ago}", today - timedelta(days=days_ago)
            )
            await _admin_seed_reply(admin, a, past_id, f"REPLYMARK{days_ago:02d} keep it moving")

        todays_id = await _seed_check_in(pool, a, "today's check-in", today)

        gate, coach = _RecordingGate(), _RecordingCoach()
        result = await reply_to_check_in(pool, a, todays_id, gate, coach)

        assert result is not None
        context = coach.last_context
        for recent in ("REPLYMARK01", "REPLYMARK02", "REPLYMARK03"):  # the three newest
            assert recent in context, f"{recent} should be one of the last three replies"
        for older in ("REPLYMARK04", "REPLYMARK05"):
            assert older not in context, f"{older} is older than the last three and must be cut"
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# =====================================================================================
# C. Storage (rows 28, 29, 30, 31)
# =====================================================================================


# AC row 28: the stored reply has role='assistant', `user_id` from UserIdDep (never a
# payload), `check_in_id` set, and non-empty content. Read from OUTSIDE RLS.
@requires_rls_db
async def test_row28_stored_reply_has_the_right_owner_role_and_parent() -> None:
    from app.db.pool import close_pool, create_pool
    from app.services.coach import reply_to_check_in

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        a_id = await _seed_check_in(pool, a, "bench 135 4x8, slept 6h")

        gate, coach = _RecordingGate(), _RecordingCoach()
        result = await reply_to_check_in(pool, a, a_id, gate, coach)
        assert result is not None
        assert result.created is True

        rows = await _admin_replies(admin, a)
        assert len(rows) == 1
        stored = rows[0]
        assert stored["role"] == "assistant"
        assert stored["user_id"] == a  # from the verified token, never the payload
        assert stored["check_in_id"] == a_id
        assert stored["content"] == COACH_TEXT
        assert stored["content"].strip() != ""
        assert result.reply.id == stored["id"]
        assert result.reply.content == COACH_TEXT
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# AC row 29: NO role='user' row is ever written. The check-in text is the user turn and
# already lives in check_ins.raw_text; duplicating a user's words into a second table is
# what this row forbids. `role` stays in the schema for when free-form chat arrives.
@requires_rls_db
async def test_row29_no_user_role_row_is_ever_written() -> None:
    from app.db.pool import close_pool, create_pool
    from app.services.coach import reply_to_check_in

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        a_id = await _seed_check_in(pool, a, "squats felt heavy today")

        gate, coach = _RecordingGate(), _RecordingCoach()
        assert await reply_to_check_in(pool, a, a_id, gate, coach) is not None

        roles = [r["role"] for r in await _admin_replies(admin, a)]
        assert roles == ["assistant"]  # exactly one row, and it is not the user's words
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# AC row 30: GET /check-ins?days=30 carries each check-in's stored reply (or null) — a reply
# must survive a refresh. (That the read is BATCHED rather than one query per check-in is
# asserted at the fake tier, tests/test_coach.py.)
@requires_rls_db
async def test_row30_a_stored_reply_comes_back_with_its_check_in() -> None:
    from app.db.pool import close_pool, create_pool
    from app.services.check_ins import list_check_ins
    from app.services.coach import reply_to_check_in

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        replied_id = await _seed_check_in(pool, a, "check-in with a reply")
        silent_id = await _seed_check_in(pool, a, "check-in with no reply")

        gate, coach = _RecordingGate(), _RecordingCoach()
        result = await reply_to_check_in(pool, a, replied_id, gate, coach)
        assert result is not None

        by_id = {row.id: row for row in await list_check_ins(pool, a, days=30)}
        assert by_id[replied_id].reply is not None
        assert by_id[replied_id].reply.content == COACH_TEXT
        assert by_id[replied_id].reply.id == result.reply.id
        assert by_id[silent_id].reply is None  # null, never a fabricated placeholder
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# AC row 31: deleting a check-in that already has a reply SUCCEEDS, and the coach_messages
# row survives with `check_in_id` now null — the applied migration says
# `on delete set null`, i.e. "chat outlives a deleted check-in".
@requires_rls_db
async def test_row31_deleting_a_check_in_keeps_the_reply_with_a_null_parent() -> None:
    from app.db.pool import close_pool, create_pool
    from app.services.check_ins import delete_check_in
    from app.services.coach import reply_to_check_in

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        a_id = await _seed_check_in(pool, a, "this check-in gets deleted")

        gate, coach = _RecordingGate(), _RecordingCoach()
        result = await reply_to_check_in(pool, a, a_id, gate, coach)
        assert result is not None

        assert await delete_check_in(pool, a, a_id) is True

        rows = await _admin_replies(admin, a)
        assert len(rows) == 1  # the reply survived its check-in
        assert rows[0]["content"] == COACH_TEXT
        assert rows[0]["check_in_id"] is None  # on delete set null
        assert rows[0]["user_id"] == a
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)
