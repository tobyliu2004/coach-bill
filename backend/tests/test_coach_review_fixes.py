"""Tests for the four PR #47 review findings Toby asked to be fixed before merge.

NOT part of the oracle — that is commit #1 (`22ea04c`), written before any implementation
existed and untouched since. These were written after the code, in response to review, and
are labelled as such so nobody mistakes them for acceptance criteria. What they are is the
thing that stops each fixed bug from coming back.

Findings covered:
  1. CONCURRENCY — two simultaneous replies to one check-in both spent Sonnet and both
     inserted. Now a partial unique index makes the loser a no-op and the service returns
     the winner's reply.
  3. RETRACTION — REMOVED BY ISSUE #48. See the audit block below.
  4. DEADLINE — gate and coach retry independently, so the real worst case was ~120s. Now
     one bound covers the whole generation.

=====================================================================================
🔓 FINDING 3'S FOUR TESTS WERE DELETED BY ISSUE #48 — APPROVED IN ADVANCE, IN THE OPEN.
=====================================================================================
The four deleted tests were:
  * test_an_off_topic_reply_can_be_retracted_and_re_asked
  * test_a_crisis_reply_can_never_be_retracted
  * test_a_real_coach_reply_cannot_be_retracted
  * test_user_b_cannot_retract_user_as_reply

`DELETE /check-ins/{id}/reply` existed for exactly one reason: to escape a wrong `off_topic`
verdict. #48 removes the label, so there is nothing left to retract and the endpoint goes
with it (#48 AC row 23 asserts it answers 405, not 404 — "the route is gone", not "the route
declined").

TWO OF THESE DELETIONS DESERVE A SENTENCE EACH, because deleting a safety test and deleting
a cross-tenant test are exactly the moves this project does not allow to happen quietly:

  * `test_a_crisis_reply_can_never_be_retracted` is replaced by something STRONGER, not by
    nothing: #48 AC row 25 (tests/test_coach.py) asserts that no `delete from
    public.coach_messages` statement exists anywhere in backend/app/, and AC row 24 revokes
    the `delete` grant from `authenticated`. The old test proved one caller refused one
    deletion; the new pair proves no caller can ask and the database would refuse anyway. A
    crisis reply is un-re-rollable BY CONSTRUCTION.
  * `test_user_b_cannot_retract_user_as_reply` was the mandatory cross-tenant row for the
    DELETE endpoint (backend.md: every endpoint that takes a resource id owes one). It dies
    with the endpoint, and no endpoint loses coverage: POST /check-ins/{id}/reply keeps its
    own cross-tenant rows against the real database in tests/test_coach_db.py
    (`test_row5_user_b_cannot_reply_to_user_as_check_in`, and row 6's zero-model-calls half).

Approved before any implementation existed, as the "what dies" section of the v2 table:
  table:    https://github.com/tobyliu2004/coach-bill/issues/48#issuecomment-5268060575
  approval: https://github.com/tobyliu2004/coach-bill/issues/48#issuecomment-5268138951

(Finding 2 — `_text_of` typing — has no runtime behaviour to assert; it is enforced by mypy.
Finding for `_num` lives in tests/test_coach_context.py.)

The real-DB tests are gated exactly like tests/test_coach_db.py, and for the same reason: a
fake pool cannot execute a unique index or a content-matched DELETE, so a fake asserting
these would only be asserting about itself.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from httpx import AsyncClient

requires_rls_db = pytest.mark.skipif(
    not os.getenv("RLS_DATABASE_URL"),
    reason="RLS_DATABASE_URL not set; coach review-fix real-DB suite skipped",
)


def _require_admin_dsn() -> str:
    dsn = os.getenv("RLS_ADMIN_DATABASE_URL")
    if not dsn:
        pytest.fail(
            "RLS_DATABASE_URL is set but RLS_ADMIN_DATABASE_URL is not; these tests need a "
            "privileged connection to seed auth.users and to read coach_messages past RLS."
        )
    return dsn


async def _admin_seed_users(admin_dsn: str, *user_ids: uuid.UUID) -> None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        for uid in user_ids:
            await conn.execute(
                "insert into auth.users (id, email) values ($1, $2) on conflict do nothing",
                uid,
                f"{uid}@review.test",
            )
    finally:
        await conn.close()


async def _admin_delete_users(admin_dsn: str, *user_ids: uuid.UUID) -> None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute("delete from auth.users where id = any($1::uuid[])", list(user_ids))
    finally:
        await conn.close()


async def _admin_replies(admin_dsn: str, user_id: uuid.UUID) -> list[str]:
    """Every stored reply for a user, read from OUTSIDE RLS.

    Counting through the user's own policy would ask the mechanism under test to grade
    itself — the same reason tests/test_coach_db.py reads with the admin DSN.
    """
    conn = await asyncpg.connect(admin_dsn)
    try:
        rows = await conn.fetch(
            "select content from public.coach_messages where user_id = $1 order by created_at",
            user_id,
        )
        return [r["content"] for r in rows]
    finally:
        await conn.close()


async def _seed_check_in(pool: asyncpg.Pool, user_id: uuid.UUID, text: str) -> uuid.UUID:
    from app.db.session import authed_conn

    async with authed_conn(pool, user_id) as conn:
        cid: uuid.UUID = await conn.fetchval(
            "insert into public.check_ins (user_id, raw_text, source, entry_date) "
            "values ($1, $2, 'text', $3) returning id",
            user_id,
            text,
            datetime.now(UTC).date(),
        )
        return cid


class _CountingGate:
    def __init__(self, label: str = "coach") -> None:
        self.label = label
        self.calls = 0

    async def classify(self, text: str) -> Any:
        from app.ai.gate import Intent

        self.calls += 1
        return Intent(label=self.label)  # type: ignore[arg-type]


class _CountingCoach:
    def __init__(self, text: str = "Solid session — keep the bar path tight.") -> None:
        self.text = text
        self.calls = 0

    async def reply(self, context: str, text: str) -> str:
        self.calls += 1
        return self.text


# `_sign_in_as` lived here and was used ONLY by finding 3's four retraction tests, which
# #48 deleted (see the audit block in the module docstring). It goes with them — a helper
# nothing calls is how a deleted feature keeps a foothold in the suite.


# =====================================================================================
# Finding 1 — concurrency
# =====================================================================================


@requires_rls_db
async def test_concurrent_replies_converge_on_exactly_one_stored_reply() -> None:
    """THE REGRESSION. Before the partial unique index, every racing request inserted.

    Ten simultaneous requests to the same check-in. Afterwards there must be exactly ONE
    row, every caller must have been handed the SAME reply, and exactly one of them must be
    told it created it.

    ⚠️ WHAT THIS DELIBERATELY DOES NOT ASSERT: that the coach was called once. It isn't —
    all ten reach the model before any of them reaches the insert, and nine then lose the
    race. That is measured explicitly at the bottom of this test rather than hidden, because
    it is the honest boundary of what a database constraint can fix: an index makes the DATA
    consistent, it cannot un-spend a model call that already happened.

    Closing the spend gap in the backend would mean holding a lock across the whole 3-6s
    generation, which pins a pooled connection per in-flight reply — a worse problem on a
    small pool than the one it solves. And it would only cover same-check-in concurrency
    while leaving ten requests against ten DIFFERENT check-ins untouched, which is the same
    money. Bounding a user's total spend is what #26's per-user caps are for; the realistic
    accident (a double-click) is handled on the client, where the action is disabled while a
    request is in flight.
    """
    from app.db.pool import close_pool, create_pool
    from app.services.coach import reply_to_check_in

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        check_in_id = await _seed_check_in(pool, a, "bench 135 4x8")
        gate, coach = _CountingGate(), _CountingCoach()

        results = await asyncio.gather(
            *(reply_to_check_in(pool, a, check_in_id, gate, coach) for _ in range(10))
        )

        assert all(r is not None for r in results), "no request should have 404'd"
        stored = await _admin_replies(admin, a)
        assert len(stored) == 1, f"expected exactly one stored reply, got {len(stored)}"

        # Every caller sees the SAME reply, and only one of them is told it created it.
        ids = {r.reply.id for r in results if r is not None}
        assert len(ids) == 1, f"callers disagreed about which reply exists: {ids}"
        assert sum(1 for r in results if r is not None and r.created) == 1

        # Recorded, not asserted-away. Every racing request DID reach the model — that is
        # the residual this fix does not close, and writing it down here means a future
        # change that does close it (a lock, a queue, #26's caps) will show up as this
        # number dropping rather than as a silent behaviour change nobody noticed.
        assert coach.calls == 10, (
            "expected every racing request to reach the coach before the insert race — if "
            "this dropped, the spend gap was closed somewhere and this comment is stale"
        )
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)


@requires_rls_db
async def test_the_unique_index_is_what_makes_that_true() -> None:
    """Watch the constraint itself fire, so the guard is one we have seen work.

    A second direct `insert_reply` returns None because `on conflict do nothing` swallowed
    it — not because the parent guard blocked it (the check-in is right there). That
    ambiguity is exactly what the service re-read exists to resolve.
    """
    from app.db.coach import insert_reply
    from app.db.pool import close_pool, create_pool

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        check_in_id = await _seed_check_in(pool, a, "squat 225 5x5")

        first = await insert_reply(pool, a, check_in_id, "first reply")
        second = await insert_reply(pool, a, check_in_id, "second reply")

        assert first is not None
        assert second is None, "the unique index did not stop a second assistant reply"
        assert await _admin_replies(admin, a) == ["first reply"]
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# =====================================================================================
# Finding 4 — one bounded deadline for the whole generation
# =====================================================================================


async def test_a_slow_vendor_hits_the_deadline_and_stores_nothing(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gate that never returns must not hold the request open for ~120s.

    The deadline is monkeypatched to something tiny so the suite doesn't actually wait; what
    is being asserted is that the bound EXISTS and that tripping it lands in the same
    503-with-nothing-stored path as every other failure — not that 60 is the right number.
    """
    import app.services.coach as coach_service
    from app.db.coach import insert_reply  # noqa: F401 - imported to assert it is untouched

    monkeypatch.setattr(coach_service, "REPLY_DEADLINE_SECONDS", 0.05)

    class _HangingGate:
        calls = 0

        async def classify(self, text: str) -> Any:
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

    inserted: list[Any] = []

    async def _fake_insert(*args: Any, **kwargs: Any) -> Any:
        inserted.append(args)
        return None

    monkeypatch.setattr(coach_service.coach_db, "insert_reply", _fake_insert)

    async def _fake_get_check_in(*args: Any, **kwargs: Any) -> Any:
        return {"id": uuid.uuid4(), "raw_text": "bench 135 4x8"}

    async def _fake_get_reply(*args: Any, **kwargs: Any) -> Any:
        return None

    monkeypatch.setattr(coach_service.check_ins_db, "get_check_in", _fake_get_check_in)
    monkeypatch.setattr(coach_service.coach_db, "get_reply_for_check_in", _fake_get_reply)

    with pytest.raises(coach_service.CoachUnavailable):
        await coach_service.reply_to_check_in(
            pool=None,  # type: ignore[arg-type]
            user_id=uuid.uuid4(),
            check_in_id=uuid.uuid4(),
            gate=_HangingGate(),
            coach=_CountingCoach(),
        )

    assert inserted == [], "a request that timed out must store nothing"


def test_the_deadline_is_finite_and_covers_both_model_calls() -> None:
    """The bound has to be smaller than the sum it replaced, or it isn't a fix.

    gate 15s x3 retries + coach 25s x3 retries was ~120s; `test_row36_real_clients_have_a_
    bounded_timeout` asserts each client separately and structurally cannot see that sum.
    """
    from app.ai.coach import _TIMEOUT_SECONDS as coach_timeout
    from app.ai.gate import _TIMEOUT_SECONDS as gate_timeout
    from app.services.coach import REPLY_DEADLINE_SECONDS

    worst_case_without_the_deadline = (gate_timeout * 3) + (coach_timeout * 3)

    assert 0 < REPLY_DEADLINE_SECONDS < worst_case_without_the_deadline
