"""Oracle suite for issue #20, PR 1 (History) — backend rows 1-16.

Written BEFORE any implementation exists, from the correctness table Toby approved on
issue #20 ("Acceptance criteria — approved correctness table (PART 1 of 2: History)").
Every test names the row it covers. Nothing here describes code — none of it is on disk:
`app.db.check_ins.list_check_ins_in_range` does not exist, and `list_check_ins` does not
take a `days` argument yet. Those import/TypeError failures are the CORRECT initial red.

Two tiers, same split as test_check_ins.py:

1. Fake-pool unit tests (rows 1-11, 14, 15). The fakes cannot execute SQL, so they prove
   the *contract*: status codes, query-param validation, response shape, and that the db
   layer is invoked with the right statement + arguments — including the `user_id` filter
   in the SAME statement, which is the security boundary (backend.md rule 2).

2. Real-DB integration tests (rows 2, 10, 12, 13, 15, 16), gated on HAS_REAL_DB. These are
   the ONLY place cross-tenant isolation (row 12) and fact-bundling-by-check_in_id (rows
   13, 16) can be proven — a fake pool cannot run the `where user_id = $1` that a leak
   would bypass, and cannot join facts onto the wrong check-in.

Deliberately NOT here:
  * Trends (`GET /trends`) — that is PR 2 and gets its own approved table.
  * The amendment to test_check_ins.py:271-275 (the three assertions that pin the old
    single-date SQL). That file is untouched by this commit; the amendment lands in its
    own commit with its own justification, exactly as the issue records.
  * The full auth negative matrix (wrong signature, algorithm confusion, wrong issuer,
    wrong audience). Row 14 asks for missing/malformed/expired on this endpoint, and they
    are below; the other three exercise the SAME shared `get_current_user_id` dependency
    and are already proven exhaustively in tests/test_auth.py. Duplicating them here would
    test the dependency twice and this endpoint no better.
"""

import base64
import json
import os
import time
import uuid
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
from httpx import AsyncClient

from app.auth import get_current_user_id, get_jwks_client
from app.main import app

USER_ID = uuid.uuid4()
CHECK_IN_ID = uuid.uuid4()
SECOND_CHECK_IN_ID = uuid.uuid4()
THIRD_CHECK_IN_ID = uuid.uuid4()
FOURTH_CHECK_IN_ID = uuid.uuid4()
CREATED_AT = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)

# The frozen server clocks. Row 3 pins the whole point: at this instant it is Aug 2 in UTC
# but still Aug 1 in Los Angeles, so a window built from the server's date and a window
# built from the USER's date are different windows — an assertion that can tell Aug 1 from
# Aug 2 is the only kind that covers this row.
LA_EVENING_UTC = datetime(2026, 8, 2, 0, 30, tzinfo=UTC)  # = Aug 1 17:30 America/Los_Angeles
MIDDAY_UTC = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)  # unambiguously Aug 1 in UTC


def _check_in_row(**overrides: Any) -> dict[str, Any]:
    """A check_ins row as asyncpg would return it (dict-like), server-owned defaults."""
    row: dict[str, Any] = {
        "id": CHECK_IN_ID,
        "raw_text": "did 5x5 squats at 225",
        "source": "text",
        "entry_date": date(2026, 8, 1),
        "created_at": CREATED_AT,
        "extraction_status": "done",
    }
    row.update(overrides)
    return row


# --- fake pool: copied in shape from test_check_ins.py (a copy, not an import, so this
# file's oracle can never be weakened by an edit to another test module). Every
# fetch-family method pops the next primed value and records (query, args), so
# pool.conn.calls holds ONLY the real queries, in order. authed_conn's identity statements
# go to identity_calls, so `calls == []` still means "the db was never asked anything".


class _FakeTxn:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FakeConn:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.identity_calls: list[tuple[str, tuple[Any, ...]]] = []

    def transaction(self) -> _FakeTxn:
        return _FakeTxn()

    async def execute(self, query: str, *args: Any) -> str:
        self.identity_calls.append((query, args))  # authed_conn's set_config / set role
        return "OK"

    def _next(self) -> Any:
        return self._responses.pop(0) if self._responses else None

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.calls.append((query, args))
        return self._next()

    async def fetchrow(self, query: str, *args: Any) -> Any:
        self.calls.append((query, args))
        return self._next()

    async def fetch(self, query: str, *args: Any) -> Any:
        self.calls.append((query, args))
        return self._next()


class FakePool:
    def __init__(self, responses: list[Any]) -> None:
        self.conn = _FakeConn(responses)

    def acquire(self) -> "_FakeAcquire":
        return _FakeAcquire(self.conn)


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


def _sign_in(responses: list[Any]) -> FakePool:
    """Wire the app as if USER_ID holds a valid token and the DB serves `responses` in order."""
    from app.deps import get_pool

    pool = FakePool(responses)
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    app.dependency_overrides[get_pool] = lambda: pool
    return pool


def _list_responses(rows: list[dict[str, Any]]) -> list[Any]:
    """Primed values for one GET: the timezone read, the range rows, then the bundled reads.

    The tz value is the caller's; pass it via `_with_tz`. Four empty fact lists trail the
    rows (workout_sets / nutrition / sleep / bodyweight, bundled since #19), plus one empty
    reply list (coach_messages, bundled since #21 — AC row 30). They pop AFTER calls[1], so
    every assertion on calls[0]/calls[1] below is unaffected by how many bundled queries the
    implementation ends up making.

    Adding the fifth trailing list is priming, not a weakened assertion: this file's tests
    assert on calls[0] (the timezone read) and calls[1] (the range query), and both are
    untouched. It is the same extension #19 made when it added the four fact reads — the
    docstring above was written to expect it.
    """
    return [rows, [], [], [], [], []]


def _with_tz(tz: str | None, rows: list[dict[str, Any]]) -> list[Any]:
    return [tz, *_list_responses(rows)]


def _freeze(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    """Pin the server clock to `moment` (an aware UTC datetime).

    Patched on `app.time` — the module that owns `local_today`, documented as the single
    source of "today" for both the write and the read — so the freeze holds no matter how
    the service imports it. If a range is ever computed from some other clock, the row-3
    and row-4 assertions below will not see this freeze and will fail. That is intended:
    "today" having two sources is exactly the #18 timezone bug.
    """
    import app.time

    class _Clock:
        @staticmethod
        def now(tz: tzinfo | None = None) -> datetime:
            return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)

    monkeypatch.setattr(app.time, "datetime", _Clock)


def _list_call(pool: FakePool) -> tuple[str, tuple[Any, ...]]:
    """The range query: call 0 is the timezone read, call 1 is the list."""
    assert len(pool.conn.calls) >= 2, (
        f"expected a timezone read then a list query, got {pool.conn.calls}"
    )
    return pool.conn.calls[1]


def _assert_owner_scoped_range(query: str) -> None:
    """The security assertion, hoisted so every window test carries it (backend.md rule 2).

    The owner filter and the date range must be in the SAME statement — a range query that
    forgot `user_id` would return every user's history, and RLS is the net, not the plan.
    The literal text is the shape issue #20 records for the refactor.
    """
    q = query.lower()
    assert "where user_id = $1" in q, q  # first lock, same statement
    assert "entry_date between $2 and $3" in q, q  # inclusive range, both ends bound


# ============================ window semantics (rows 1-11) ============================


# AC row 1 (REGRESSION GUARD): GET /check-ins with no params -> 200 and exactly today's
# rows, newest first. The window collapses to a single day: start == end == the caller's
# local today, byte-identical in effect to the pre-refactor single-date query.
async def test_no_params_defaults_to_today_only(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, MIDDAY_UTC)
    newest = _check_in_row(id=CHECK_IN_ID, raw_text="newest")
    older = _check_in_row(
        id=SECOND_CHECK_IN_ID, raw_text="older", created_at=CREATED_AT - timedelta(hours=2)
    )
    pool = _sign_in(_with_tz("UTC", [newest, older]))

    resp = await client.get("/check-ins")

    assert resp.status_code == 200
    body = resp.json()
    assert [r["id"] for r in body] == [str(CHECK_IN_ID), str(SECOND_CHECK_IN_ID)]

    query, args = _list_call(pool)
    _assert_owner_scoped_range(query)
    assert args == (USER_ID, date(2026, 8, 1), date(2026, 8, 1))  # today..today, not today..±1


# AC row 2 (JUDGMENT CALL — the off-by-one): days=7 with the user's today = Aug 1 means
# Jul 26..Aug 1 INCLUSIVE (today + 6 prior). Jul 25 is outside the window; Aug 1 is inside.
async def test_days_7_window_is_today_plus_six_prior(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, MIDDAY_UTC)
    pool = _sign_in(_with_tz("UTC", [_check_in_row()]))

    resp = await client.get("/check-ins?days=7")

    assert resp.status_code == 200
    query, args = _list_call(pool)
    _assert_owner_scoped_range(query)
    # Jul 26 not Jul 25 (that would be 8 days) and not Jul 27 (that would be 6).
    assert args == (USER_ID, date(2026, 7, 26), date(2026, 8, 1))


# AC row 3 (the #18 timezone trap, on a range): tz America/Los_Angeles, server clock
# Aug 2 00:30 UTC = Aug 1 17:30 LA, days=1 -> the window is the LA date Aug 1..Aug 1.
# Aug 2 anywhere in the args means the user's evening check-in vanished from "today".
async def test_window_uses_the_users_local_date_not_the_server_date(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, LA_EVENING_UTC)
    pool = _sign_in(_with_tz("America/Los_Angeles", [_check_in_row()]))

    resp = await client.get("/check-ins?days=1")

    assert resp.status_code == 200
    query, args = _list_call(pool)
    _assert_owner_scoped_range(query)
    assert args == (USER_ID, date(2026, 8, 1), date(2026, 8, 1))
    assert date(2026, 8, 2) not in args  # the server's UTC date must not leak into the window


# AC row 4 (seatbelt): profiles.timezone is NULL -> the window is computed in UTC and the
# request succeeds. At the frozen instant UTC is already Aug 2, which is what distinguishes
# "fell back to UTC" from "silently reused some other zone".
async def test_null_timezone_falls_back_to_utc_without_crashing(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, LA_EVENING_UTC)
    pool = _sign_in(_with_tz(None, [_check_in_row(entry_date=date(2026, 8, 2))]))

    resp = await client.get("/check-ins?days=1")

    assert resp.status_code == 200
    query, args = _list_call(pool)
    _assert_owner_scoped_range(query)
    assert args == (USER_ID, date(2026, 8, 2), date(2026, 8, 2))


# AC row 5: days=0 -> 422. A zero-day window is meaningless; coercing it to 1 would hide a
# client bug. Nothing may reach the database.
async def test_days_zero_is_422(client: AsyncClient) -> None:
    pool = _sign_in(_with_tz("UTC", [_check_in_row()]))

    resp = await client.get("/check-ins?days=0")

    assert resp.status_code == 422
    assert pool.conn.calls == []


# AC row 6: days=-1 -> 422. A negative window would invert the BETWEEN and return an empty
# screen with no error at all.
async def test_days_negative_is_422(client: AsyncClient) -> None:
    pool = _sign_in(_with_tz("UTC", [_check_in_row()]))

    resp = await client.get("/check-ins?days=-1")

    assert resp.status_code == 422
    assert pool.conn.calls == []


# AC row 7 (JUDGMENT CALL): days=366 -> 422. The cap is the only bound on payload size —
# there is no pagination in v1.
async def test_days_366_is_422(client: AsyncClient) -> None:
    pool = _sign_in(_with_tz("UTC", [_check_in_row()]))

    resp = await client.get("/check-ins?days=366")

    assert resp.status_code == 422
    assert pool.conn.calls == []


# AC row 8 (JUDGMENT CALL — the cap is INCLUSIVE): days=365 -> 200, and the window really
# spans 365 days ending today. 200-with-a-truncated-window would satisfy a status-only
# assertion, so the args are pinned too.
async def test_days_365_is_accepted_and_spans_365_days(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, MIDDAY_UTC)
    pool = _sign_in(_with_tz("UTC", [_check_in_row()]))

    resp = await client.get("/check-ins?days=365")

    assert resp.status_code == 200
    query, args = _list_call(pool)
    _assert_owner_scoped_range(query)
    # 2025-08-02 .. 2026-08-01 inclusive is exactly 365 dates (2026 is not a leap year).
    assert args == (USER_ID, date(2025, 8, 2), date(2026, 8, 1))


# AC row 9: days=abc -> 422. First query param in the codebase; the validation has to be
# wired, not merely declared.
async def test_days_non_integer_is_422(client: AsyncClient) -> None:
    pool = _sign_in(_with_tz("UTC", [_check_in_row()]))

    resp = await client.get("/check-ins?days=abc")

    assert resp.status_code == 422
    assert pool.conn.calls == []


# AC row 10: newest entry_date first, and within a day newest created_at first. The db does
# the ORDER BY (asserted on the statement); the service must not resort or reverse it
# (asserted by echoing the primed order). The behavioral proof is the real-DB test below.
async def test_rows_are_ordered_by_day_then_time_desc(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, MIDDAY_UTC)
    aug1 = _check_in_row(id=CHECK_IN_ID, entry_date=date(2026, 8, 1), created_at=CREATED_AT)
    jul31_late = _check_in_row(
        id=SECOND_CHECK_IN_ID,
        entry_date=date(2026, 7, 31),
        created_at=datetime(2026, 7, 31, 20, 0, tzinfo=UTC),
    )
    jul31_early = _check_in_row(
        id=THIRD_CHECK_IN_ID,
        entry_date=date(2026, 7, 31),
        created_at=datetime(2026, 7, 31, 7, 0, tzinfo=UTC),
    )
    jul30 = _check_in_row(
        id=FOURTH_CHECK_IN_ID,
        entry_date=date(2026, 7, 30),
        created_at=datetime(2026, 7, 30, 9, 0, tzinfo=UTC),
    )
    pool = _sign_in(_with_tz("UTC", [aug1, jul31_late, jul31_early, jul30]))

    resp = await client.get("/check-ins?days=7")

    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [
        str(CHECK_IN_ID),
        str(SECOND_CHECK_IN_ID),
        str(THIRD_CHECK_IN_ID),
        str(FOURTH_CHECK_IN_ID),
    ]
    query, _ = _list_call(pool)
    assert "order by entry_date desc, created_at desc" in query.lower()


# AC row 11: no check-ins anywhere in a 30-day window -> 200 and []. Empty is not an error;
# a 404 would make the screen render a failure state over a perfectly good answer.
async def test_empty_window_is_200_and_empty_list(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, MIDDAY_UTC)
    pool = _sign_in(_with_tz("UTC", []))

    resp = await client.get("/check-ins?days=30")

    assert resp.status_code == 200
    assert resp.json() == []
    query, args = _list_call(pool)
    _assert_owner_scoped_range(query)
    assert args == (USER_ID, date(2026, 7, 3), date(2026, 8, 1))  # 30 days ending today


# AC row 15: a check-in whose extraction FAILED is still in the window, with its raw_text
# and empty facts. The text is the source of truth — a dead extraction must never make a
# day disappear from history. (The SQL half of this row is proven on the real DB below.)
async def test_failed_extraction_check_in_still_appears_with_its_text(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, MIDDAY_UTC)
    failed = _check_in_row(raw_text="squats felt awful", extraction_status="failed")
    pool = _sign_in(_with_tz("UTC", [failed]))

    resp = await client.get("/check-ins?days=30")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == str(CHECK_IN_ID)
    assert body[0]["raw_text"] == "squats felt awful"  # the text survives the failure
    assert body[0]["extraction_status"] == "failed"
    assert body[0]["facts"] == {"sets": [], "nutrition": [], "sleep": [], "bodyweight": []}

    # ...and it was found *inside the window the caller asked for*. Without this, the test
    # would pass against an implementation that ignores `days` entirely (today's code does),
    # which would make it a green oracle rather than a red one.
    query, args = _list_call(pool)
    _assert_owner_scoped_range(query)
    assert args == (USER_ID, date(2026, 7, 3), date(2026, 8, 1))


# ================================ auth (row 14) ================================
#
# Real ES256 tokens, verified through the real dependency against a fake JWKS client —
# copied in shape from test_auth.py so this endpoint's negative paths are proven on this
# endpoint. Must match the SUPABASE_URL conftest.py sets before the app imports.

_ISSUER = "https://test.supabase.co/auth/v1"
_PRIVATE_KEY: EllipticCurvePrivateKey = ec.generate_private_key(ec.SECP256R1())
_PUBLIC_KEY = _PRIVATE_KEY.public_key()


class _FakeJWKSClient:
    def get_signing_key_from_jwt(self, token: str) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(key=_PUBLIC_KEY)


def _use_fake_jwks() -> None:
    app.dependency_overrides[get_jwks_client] = lambda: _FakeJWKSClient()


def _make_token(*, expires_in: int = 3600) -> str:
    claims: dict[str, Any] = {
        "aud": "authenticated",
        "iss": _ISSUER,
        "exp": int(time.time()) + expires_in,
        "sub": str(uuid.uuid4()),
    }
    token: str = jwt.encode(claims, _PRIVATE_KEY, algorithm="ES256")
    return token


# AC row 14: no Authorization header -> 401 (and the RFC 6750 challenge header).
async def test_history_without_token_is_401(client: AsyncClient) -> None:
    _use_fake_jwks()

    resp = await client.get("/check-ins?days=30")

    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"


# AC row 14: a malformed token -> 401, not a 500.
async def test_history_with_malformed_token_is_401(client: AsyncClient) -> None:
    _use_fake_jwks()

    resp = await client.get("/check-ins?days=30", headers={"Authorization": "Bearer not.a.jwt"})

    assert resp.status_code == 401


# AC row 14: a token with an unreadable (non-base64) payload -> 401, not a 500.
async def test_history_with_garbage_bearer_value_is_401(client: AsyncClient) -> None:
    _use_fake_jwks()
    header = base64.urlsafe_b64encode(json.dumps({"alg": "ES256"}).encode()).rstrip(b"=").decode()

    resp = await client.get(
        "/check-ins?days=30", headers={"Authorization": f"Bearer {header}.%%%.%%%"}
    )

    assert resp.status_code == 401


# AC row 14: a properly-signed but EXPIRED token -> 401. Everything about it is valid
# except the clock, so this proves expiry is actually checked.
async def test_history_with_expired_token_is_401(client: AsyncClient) -> None:
    _use_fake_jwks()

    resp = await client.get(
        "/check-ins?days=30",
        headers={"Authorization": f"Bearer {_make_token(expires_in=-60)}"},
    )

    assert resp.status_code == 401


# ===================== real-DB integration (rows 2, 10, 12, 13, 15, 16) =====================
#
# Gated exactly like test_check_ins.py's. These run against LOCAL Supabase and are the only
# tests that execute the range SQL, so they are the only proof of cross-tenant isolation
# over a window (row 12) and of facts bundling by check_in_id (rows 13, 16).

requires_db = pytest.mark.skipif(
    not os.getenv("HAS_REAL_DB"),
    reason="no real DATABASE_URL set; real-database integration test skipped",
)


async def _seed_users(pool: Any, *user_ids: uuid.UUID) -> None:
    """Insert minimal auth.users rows; fail loudly (not silently) if the schema needs more."""
    try:
        async with pool.acquire() as conn:
            for uid in user_ids:
                await conn.execute(
                    "insert into auth.users (id, email) values ($1, $2) on conflict do nothing",
                    uid,
                    f"{uid}@test.example",
                )
    except Exception as exc:  # noqa: BLE001 - surface the real seed error to the report
        pytest.fail(f"failed to seed auth.users (adjust seed at /ship if columns changed): {exc}")


async def _delete_users(pool: Any, *user_ids: uuid.UUID) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "delete from auth.users where id = any($1::uuid[])",
            list(user_ids),
        )


async def _insert_set(
    pool: Any, user_id: uuid.UUID, check_in_id: uuid.UUID, reps: int
) -> uuid.UUID:
    """Attach one workout_sets row to a check-in, straight to SQL.

    Deliberately not routed through the extraction service: this is a FIXTURE, and going
    through the AI pipeline would make rows 13/16 depend on extraction behavior they are
    not about. 'squat' is in the seeded catalog (20260716184500_extraction.sql).
    """
    async with pool.acquire() as conn:
        exercise_id = await conn.fetchval("select id from public.exercises where name = 'squat'")
        assert exercise_id is not None, "seeded exercise catalog is missing 'squat'"
        set_id: uuid.UUID = await conn.fetchval(
            "insert into public.workout_sets"
            " (user_id, check_in_id, exercise_id, set_number, reps, weight_kg)"
            " values ($1, $2, $3, 1, $4, 100) returning id",
            user_id,
            check_in_id,
            exercise_id,
            reps,
        )
    return set_id


# AC row 2 (behavioral, end-to-end): with days=7, the row dated today-6 is IN the window
# and the row dated today-7 is OUT. The fake-tier test pins the arguments; this one proves
# the SQL actually excludes the eighth day.
@requires_db
async def test_seven_day_window_includes_day_six_and_excludes_day_seven() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool
    from app.services.check_ins import list_check_ins

    a = uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a)
        today = datetime.now(UTC).date()  # A has no timezone -> local today is UTC today
        inside = await insert_check_in(pool, a, "six days ago", today - timedelta(days=6))
        outside = await insert_check_in(pool, a, "seven days ago", today - timedelta(days=7))

        ids = [r.id for r in await list_check_ins(pool, a, days=7)]

        assert inside["id"] in ids
        assert outside["id"] not in ids
    finally:
        await _delete_users(pool, a)
        await close_pool(pool)


# AC row 10 (behavioral, end-to-end): three days, two rows on the middle day -> newest day
# first, and within that day the later created_at first.
@requires_db
async def test_ordering_is_newest_day_then_newest_within_the_day() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool
    from app.services.check_ins import list_check_ins

    a = uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a)
        today = datetime.now(UTC).date()
        oldest_day = await insert_check_in(pool, a, "two days ago", today - timedelta(days=2))
        middle_first = await insert_check_in(
            pool, a, "yesterday morning", today - timedelta(days=1)
        )
        middle_second = await insert_check_in(
            pool, a, "yesterday evening", today - timedelta(days=1)
        )
        newest_day = await insert_check_in(pool, a, "today", today)

        ids = [r.id for r in await list_check_ins(pool, a, days=7)]

        assert ids == [
            newest_day["id"],
            middle_second["id"],  # same day, inserted later -> later created_at -> first
            middle_first["id"],
            oldest_day["id"],
        ]
    finally:
        await _delete_users(pool, a)
        await close_pool(pool)


# AC row 12 (MANDATORY cross-tenant): A and B both have check-ins across the window; B's
# 30-day history contains only B's rows and none of A's ids — and A's rows are unchanged.
# A wider window is a wider leak, so this is the row that has to run against real SQL.
@requires_db
async def test_history_window_is_isolated_per_user() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool
    from app.services.check_ins import list_check_ins

    a, b = uuid.uuid4(), uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a, b)
        today = datetime.now(UTC).date()
        a_today = await insert_check_in(pool, a, "A today", today)
        a_old = await insert_check_in(pool, a, "A ten days ago", today - timedelta(days=10))
        b_today = await insert_check_in(pool, b, "B today", today)
        b_old = await insert_check_in(pool, b, "B ten days ago", today - timedelta(days=10))

        b_rows = await list_check_ins(pool, b, days=30)
        b_ids = [r.id for r in b_rows]

        assert sorted(map(str, b_ids)) == sorted(map(str, [b_today["id"], b_old["id"]]))
        assert a_today["id"] not in b_ids
        assert a_old["id"] not in b_ids
        assert all(r.raw_text not in ("A today", "A ten days ago") for r in b_rows)

        # ...and A still has both of its rows, untouched by B's read.
        a_ids = [r.id for r in await list_check_ins(pool, a, days=30)]
        assert sorted(map(str, a_ids)) == sorted(map(str, [a_today["id"], a_old["id"]]))
    finally:
        await _delete_users(pool, a, b)
        await close_pool(pool)


# AC row 13: A's check-in has workout sets; B requests a 30-day history. NO fact row
# belonging to A appears anywhere in B's response. Facts are bundled by check_in_id, so a
# bundling query that forgot the owner filter would attach A's private numbers to B's
# screen — and the wider the window, the more of them.
@requires_db
async def test_no_fact_row_of_another_user_appears_in_a_history_window() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool
    from app.services.check_ins import list_check_ins

    a, b = uuid.uuid4(), uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a, b)
        today = datetime.now(UTC).date()
        a_check_in = await insert_check_in(pool, a, "A squats", today - timedelta(days=3))
        a_set_id = await _insert_set(pool, a, a_check_in["id"], reps=5)
        b_check_in = await insert_check_in(pool, b, "B squats", today - timedelta(days=3))
        b_set_id = await _insert_set(pool, b, b_check_in["id"], reps=8)

        b_rows = await list_check_ins(pool, b, days=30)

        all_b_set_ids = [s.id for r in b_rows for s in r.facts.sets]
        assert a_set_id not in all_b_set_ids  # A's private numbers are nowhere in B's payload
        assert all_b_set_ids == [b_set_id]  # ...and B still sees its own
        assert all(r.id != a_check_in["id"] for r in b_rows)
    finally:
        await _delete_users(pool, a, b)
        await close_pool(pool)


# AC row 15 (the SQL half): a check-in with extraction_status = 'failed' and zero fact rows
# is STILL returned by the window query, with its raw_text. An inner join onto the fact
# tables would silently drop it and make the whole day disappear from history.
@requires_db
async def test_failed_check_in_is_not_dropped_by_the_range_query() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool
    from app.services.check_ins import list_check_ins

    a = uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a)
        today = datetime.now(UTC).date()
        row = await insert_check_in(
            pool, a, "tried to squat, phone died", today - timedelta(days=2)
        )
        async with pool.acquire() as conn:
            await conn.execute(
                "update public.check_ins set extraction_status = 'failed' where id = $1",
                row["id"],
            )

        rows = await list_check_ins(pool, a, days=30)

        found = [r for r in rows if r.id == row["id"]]
        assert len(found) == 1, "a failed extraction must not remove the day from history"
        assert found[0].raw_text == "tried to squat, phone died"
        assert found[0].extraction_status == "failed"
        assert found[0].facts.sets == []
    finally:
        await _delete_users(pool, a)
        await close_pool(pool)


# AC row 16: two check-ins on DIFFERENT days, each with its own sets -> each check-in's
# facts attach only to that check-in. Bleed across days would show yesterday's lifts under
# today, which is indistinguishable from a data-integrity bug to the user.
@requires_db
async def test_facts_attach_only_to_their_own_check_in_across_days() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool
    from app.services.check_ins import list_check_ins

    a = uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a)
        today = datetime.now(UTC).date()
        today_row = await insert_check_in(pool, a, "today squats", today)
        today_set = await _insert_set(pool, a, today_row["id"], reps=5)
        yesterday_row = await insert_check_in(
            pool, a, "yesterday squats", today - timedelta(days=1)
        )
        yesterday_set = await _insert_set(pool, a, yesterday_row["id"], reps=12)

        by_id = {r.id: r for r in await list_check_ins(pool, a, days=30)}

        assert [s.id for s in by_id[today_row["id"]].facts.sets] == [today_set]
        assert [s.id for s in by_id[yesterday_row["id"]].facts.sets] == [yesterday_set]
        # the distinguishing detail: the reps that identify each day's set never cross over
        assert [s.reps for s in by_id[today_row["id"]].facts.sets] == [5]
        assert [s.reps for s in by_id[yesterday_row["id"]].facts.sets] == [12]
    finally:
        await _delete_users(pool, a)
        await close_pool(pool)
