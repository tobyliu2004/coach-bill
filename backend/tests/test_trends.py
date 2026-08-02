"""Oracle suite for issue #20, PR 2 (Trends) — backend rows 1-26.

Written BEFORE any implementation exists, from the correctness table Toby approved on
issue #20 ("Acceptance criteria — approved correctness table (PART 2 of 2: Trends)").
Every test names the row it covers. Nothing here describes code — none of it is on disk:
`app.schemas.trends`, `app.routes.trends`, `app.services.trends` and `app.db.trends` do
not exist, and neither does `GET /trends`. Those 404s and ImportErrors are the CORRECT
initial red. No stub was created to make anything resolve.

Three tiers, and the split is deliberate rather than cosmetic:

1. Fake-pool contract tests (rows 1-7, 25). The fakes cannot execute SQL, so they prove
   only what does not need SQL: status codes, query-param validation, the echoed window,
   the empty-series payload, and the literal owner-scoping of every statement the db layer
   issues. Row 25 lives here because it is a claim ABOUT the statement text, not about
   what the statement returns.

2. Real ES256 tokens through the real auth dependency (row 26) — copied in shape from
   tests/test_auth.py, so this endpoint's own negative paths are proven on this endpoint.

3. Real-DB integration (rows 3, 8-24), gated on HAS_REAL_DB. **Every aggregation row is
   here, not in the fake tier.** A fake-pool test for "4x8 @ 100 kg -> 3200" would have to
   prime a record that already says 3200 and then assert that 3200 came back — an
   assertion that passes against literally any SQL, including SQL that sums a bodyweight
   set into the tonnage. Rows 10-21 are claims about what the *database* computes, so the
   only honest oracle for them executes SQL. Same for rows 22-24 (cross-tenant) and rows
   8/9 (sparse + oldest-first).

Deliberately NOT here:
  * The full auth negative matrix (wrong signature, algorithm confusion, wrong issuer,
    wrong audience). Row 26 asks for missing/malformed/expired on this endpoint and they
    are below; the other three exercise the SAME shared `get_current_user_id` dependency
    and are proven exhaustively in tests/test_auth.py. Duplicating them would test the
    dependency twice and this endpoint no better.
  * Anything about the /trends SCREEN. Rows 27-36 are pure frontend functions and live in
    frontend/src/lib/trends.test.ts, dates.test.ts, history.test.ts, checkInView.test.ts,
    api.test.ts and auth/destination.test.ts.
"""

import base64
import json
import os
import re
import time
import uuid
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
from httpx import AsyncClient

from app.auth import get_current_user_id, get_jwks_client
from app.main import app

USER_ID = uuid.uuid4()

# The frozen server clocks. Row 3 is the whole point of having two: at LA_EVENING_UTC it is
# already Aug 2 in UTC but still Aug 1 in Los Angeles, so a window built from the server's
# date and a window built from the USER's date are different windows. Only an assertion that
# can tell Aug 1 from Aug 2 covers that row.
LA_EVENING_UTC = datetime(2026, 8, 2, 0, 30, tzinfo=UTC)  # = Aug 1 17:30 America/Los_Angeles
MIDDAY_UTC = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)  # unambiguously Aug 1 in UTC

# The four user-owned fact tables a trends statement can touch. `exercises` is the one
# ownerless table (a shared catalog, `.claude/rules/backend.md`) and is deliberately absent:
# it carries no `user_id` and must not be asserted to have one.
_FACT_TABLES = ("workout_sets", "nutrition_entries", "sleep_entries", "bodyweight_entries")


# --- fake pool: copied in shape from test_check_in_history.py (a COPY, not an import, so
# this file's oracle can never be weakened by an edit to another test module). Every
# fetch-family method records (query, args), so pool.conn.calls holds ONLY real queries, in
# order. authed_conn's identity statements go to identity_calls, so `calls == []` still
# means "the database was never asked anything".
#
# One difference from PR 1's copy, and the reason for it: `fetch` returns [] once the primed
# list is exhausted instead of None. The trends read issues several `fetch` calls whose ORDER
# the correctness table does not fix, so priming them positionally would make this tier
# depend on an ordering no row states. Every fake-tier test below therefore wants "all series
# empty", and gets it regardless of the order the implementation asks in.


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
        value = self._next()
        return [] if value is None else value


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


def _sign_in(tz: str | None) -> FakePool:
    """Wire the app as if USER_ID holds a valid token, with `tz` on their profile.

    The single primed value is the timezone read (a `fetchval`); every fact `fetch` then
    falls through to []. So the whole fake tier describes "a user with a profile and no
    facts", which is exactly the state rows 1-7 and 25 are about.
    """
    from app.deps import get_pool

    pool = FakePool([tz])
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    app.dependency_overrides[get_pool] = lambda: pool
    return pool


def _freeze(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    """Pin the server clock to `moment` (an aware UTC datetime).

    Patched on `app.time` — the module that owns `local_today`, documented as the single
    source of "today" for both the write and the read — so the freeze holds no matter how
    the service imports it. If the window is ever computed from some other clock, the row-3
    and row-4 assertions will not see this freeze and will fail. That is intended: "today"
    having two sources is exactly the #18 timezone bug, now on aggregates.
    """
    import app.time

    class _Clock:
        @staticmethod
        def now(tz: tzinfo | None = None) -> datetime:
            return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)

    monkeypatch.setattr(app.time, "datetime", _Clock)


# ---------------------------------------------------------------------------------------
# Row 25's machinery. Read this before changing it.
#
# tests/test_data_isolation.py regex-searches a WHOLE statement for the word `user_id`, so
#     from public.workout_sets ws join public.check_ins ci on ci.id = ws.check_in_id
#      where ws.user_id = $1
# passes it silently — `user_id` appears, and the tripwire cannot tell which side of the
# join it fences. That statement leaks: `ci` is unfiltered, so a check-in belonging to
# anyone can supply the `entry_date` a row is bucketed under. PR 1's
# `_assert_owner_scoped_range` has the same shape of weakness (two loose `in q` substrings).
#
# So these helpers assert CONTIGUOUS clauses — `<alias>.user_id = $1` as one unbroken
# string — on BOTH sides of the join, plus the window itself. Aliases are read out of the
# statement rather than hardcoded: the approved row spells them `ws` / `ci`, but the
# security property is "both sides are filtered", not "the author picked these two letters",
# and only the four fact tables have fixed names.
# ---------------------------------------------------------------------------------------

# Words that follow a table name when it has no alias at all (`from public.check_ins where`).
_ALIAS_STOPWORDS = frozenset(
    {
        "as",
        "cross",
        "full",
        "group",
        "having",
        "inner",
        "join",
        "left",
        "limit",
        "natural",
        "on",
        "order",
        "right",
        "union",
        "using",
        "where",
        "window",
    }
)


def _normalize(query: str) -> str:
    """Lowercase, collapse whitespace, and standardise spacing around `=`.

    Statements are built by concatenating adjacent literals, so a clause can be split across
    Python source lines; normalising is what makes "contiguous" mean contiguous in the SQL
    rather than contiguous in the file. Spacing around `=` is style, not security, so
    `ws.user_id=$1` and `ws.user_id = $1` are treated as the same clause — nothing else is.
    """
    collapsed = " ".join(query.lower().split())
    return re.sub(r"\s*=\s*", " = ", collapsed)


def _alias_for(query: str, table: str) -> str | None:
    """The alias `public.<table>` is bound to in this statement, or the bare table name."""
    match = re.search(rf"public\.{table}\s+(?:as\s+)?(\w+)", query)
    if match is None:
        return None
    alias = match.group(1)
    return table if alias in _ALIAS_STOPWORDS else alias


def _assert_owner_scoped_on_both_sides(query: str, fact_table: str) -> None:
    """The row-25 assertion: both sides of the join filtered, and the window bound.

    Every trends statement aggregates a user-owned fact table over `check_ins.entry_date`,
    so it necessarily joins the two — and both are user-owned, so both need the filter in
    the SAME statement (backend.md rule 2). `$1` on both is what makes them the same user.
    """
    q = _normalize(query)
    fact_alias = _alias_for(q, fact_table)
    check_ins_alias = _alias_for(q, "check_ins")

    assert fact_alias is not None, f"expected public.{fact_table} in this statement: {q}"
    assert check_ins_alias is not None, (
        "every trends statement must join public.check_ins — the window lives on its "
        f"entry_date, and it is user-owned, so it needs its own owner filter: {q}"
    )
    # The fact side. Without it, one user's sets are aggregated under another's dates.
    assert f"{fact_alias}.user_id = $1" in q, (
        f"missing a CONTIGUOUS owner filter on public.{fact_table} (alias {fact_alias!r}): {q}"
    )
    # The check-in side. This is the one test_data_isolation.py cannot see: the word
    # `user_id` already appears above, so its regex is satisfied while this side is open.
    assert f"{check_ins_alias}.user_id = $1" in q, (
        f"missing a CONTIGUOUS owner filter on public.check_ins (alias {check_ins_alias!r}) — "
        f"the join's other side is unfenced and test_data_isolation.py cannot see it: {q}"
    )
    # ...and the window is bound at both ends, on the same statement, inclusively.
    assert f"{check_ins_alias}.entry_date between $2 and $3" in q, q


def _trends_statements(pool: FakePool) -> list[tuple[str, tuple[Any, ...]]]:
    """Every statement this request issued against a user-owned FACT table.

    Identified by the table it touches rather than by position, because the correctness
    table fixes neither the order nor the count of the reads — only that every one of them
    is owner-scoped. The timezone read (public.profiles) is PR 1's code and is excluded.
    """
    return [
        (query, args)
        for query, args in pool.conn.calls
        if any(f"public.{table}" in _normalize(query) for table in _FACT_TABLES)
    ]


def _window_of(pool: FakePool) -> tuple[date, date]:
    """The (start, end) every trends statement was given — asserted identical across them."""
    statements = _trends_statements(pool)
    assert statements, f"no trends statement was issued at all; calls were {pool.conn.calls}"
    windows = {(args[1], args[2]) for _, args in statements}
    assert len(windows) == 1, f"the five reads disagree about the window: {windows}"
    start, end = windows.pop()
    assert isinstance(start, date) and isinstance(end, date), (start, end)
    return start, end


# ===================== A. window, params, payload shape (rows 1-7) =====================


# AC row 1: GET /trends with no params -> 200, and the window is the 30 local days ending
# today. Both halves are pinned: what the response SAYS the window is, and what the database
# was actually ASKED for. A payload that echoes Jul 3..Aug 1 while querying something else
# would satisfy either one alone.
async def test_no_params_defaults_to_thirty_local_days_ending_today(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, MIDDAY_UTC)
    pool = _sign_in("UTC")

    resp = await client.get("/trends")

    assert resp.status_code == 200
    body = resp.json()
    # Jul 3..Aug 1 inclusive is exactly 30 dates — today plus the 29 before it, the same
    # inclusive reading /history uses, so the two screens describe the same span.
    assert body["start_date"] == "2026-07-03"
    assert body["end_date"] == "2026-08-01"
    assert _window_of(pool) == (date(2026, 7, 3), date(2026, 8, 1))


# AC row 2: days=7 with the user's today = Aug 1 -> start_date Jul 26, end_date Aug 1.
# Today plus the six before it. Jul 25 would be eight days and Jul 27 six; a different
# off-by-one here than on /history would be worse than either alone.
async def test_days_7_window_is_today_plus_six_prior(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, MIDDAY_UTC)
    pool = _sign_in("UTC")

    resp = await client.get("/trends?days=7")

    assert resp.status_code == 200
    body = resp.json()
    assert body["start_date"] == "2026-07-26"
    assert body["end_date"] == "2026-08-01"
    assert _window_of(pool) == (date(2026, 7, 26), date(2026, 8, 1))


# AC row 3 (the timezone trap, on aggregates): tz America/Los_Angeles with the server clock
# at Aug 2 00:30 UTC = Aug 1 17:30 LA -> the window ENDS Aug 1, the user's date. Aug 2
# appearing anywhere means an LA user's evening session was aggregated into a day they have
# not lived yet. (That an Aug-1-LA check-in's facts are then actually inside the window is
# the behavioural half, proven against real SQL further down.)
async def test_window_ends_on_the_users_local_date_not_the_server_date(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, LA_EVENING_UTC)
    pool = _sign_in("America/Los_Angeles")

    resp = await client.get("/trends?days=30")

    assert resp.status_code == 200
    assert resp.json()["end_date"] == "2026-08-01"
    start, end = _window_of(pool)
    assert (start, end) == (date(2026, 7, 3), date(2026, 8, 1))
    assert date(2026, 8, 2) not in (start, end)  # the server's UTC date must not leak in


# AC row 4 (seatbelt): profiles.timezone is NULL -> the window is computed in UTC and the
# request succeeds. At the frozen instant UTC is already Aug 2, which is what distinguishes
# "fell back to UTC" from "silently reused some other zone".
async def test_null_timezone_falls_back_to_utc_without_crashing(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, LA_EVENING_UTC)
    pool = _sign_in(None)

    resp = await client.get("/trends?days=30")

    assert resp.status_code == 200
    assert resp.json()["end_date"] == "2026-08-02"
    assert _window_of(pool) == (date(2026, 7, 4), date(2026, 8, 2))


# AC row 5: days=0 -> 422, and NO query is executed. `calls == []` is the load-bearing half:
# a 422 rendered after the read already ran still costs a database round trip on every
# malformed request, and the bounds are the only guard on payload size.
async def test_days_zero_is_422_and_queries_nothing(client: AsyncClient) -> None:
    pool = _sign_in("UTC")

    resp = await client.get("/trends?days=0")

    assert resp.status_code == 422
    assert pool.conn.calls == []


# AC row 5: days=-1 -> 422, nothing queried. A negative window would invert the BETWEEN and
# return an empty dashboard with no error at all.
async def test_days_negative_is_422_and_queries_nothing(client: AsyncClient) -> None:
    pool = _sign_in("UTC")

    resp = await client.get("/trends?days=-1")

    assert resp.status_code == 422
    assert pool.conn.calls == []


# AC row 5: days=366 -> 422, nothing queried. The cap is the only bound on response size —
# there is no pagination in v1, and this endpoint returns five series at once.
async def test_days_366_is_422_and_queries_nothing(client: AsyncClient) -> None:
    pool = _sign_in("UTC")

    resp = await client.get("/trends?days=366")

    assert resp.status_code == 422
    assert pool.conn.calls == []


# AC row 5: days=abc -> 422, nothing queried. Validation has to be wired, not merely declared.
async def test_days_non_integer_is_422_and_queries_nothing(client: AsyncClient) -> None:
    pool = _sign_in("UTC")

    resp = await client.get("/trends?days=abc")

    assert resp.status_code == 422
    assert pool.conn.calls == []


# AC row 6 (the cap is INCLUSIVE): days=365 -> 200, and the window really spans 365 days
# ending today. A 200 over a silently truncated window would satisfy a status-only assertion,
# so the dates are pinned too.
async def test_days_365_is_accepted_and_spans_365_days(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, MIDDAY_UTC)
    pool = _sign_in("UTC")

    resp = await client.get("/trends?days=365")

    assert resp.status_code == 200
    body = resp.json()
    # 2025-08-02 .. 2026-08-01 inclusive is exactly 365 dates (2026 is not a leap year).
    assert body["start_date"] == "2025-08-02"
    assert body["end_date"] == "2026-08-01"
    assert _window_of(pool) == (date(2025, 8, 2), date(2026, 8, 1))


# AC row 7: a user with zero facts in the window -> 200, the window still comes back, and
# EVERY series is []. Empty is not an error, and the screen has to be able to say *which*
# window is empty — a payload that dropped start_date/end_date when there was nothing to
# chart would make "you logged nothing in July" unsayable.
async def test_empty_window_is_200_with_the_window_and_five_empty_series(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, MIDDAY_UTC)
    _sign_in("UTC")

    resp = await client.get("/trends?days=30")

    assert resp.status_code == 200
    assert resp.json() == {
        "start_date": "2026-07-03",
        "end_date": "2026-08-01",
        "volume": [],
        "exercises": [],
        "sleep": [],
        "bodyweight": [],
        "nutrition": [],
    }


# =========================== C. isolation — the SQL text (row 25) ===========================


# AC row 25 (the guard on the guard): prove this file's tripwire actually TRIPS.
#
# The one test here that is green from the first commit, deliberately — it takes no
# implementation, only the helper above. Row 25 exists because the EXISTING tripwire
# (test_data_isolation.py) is too weak to see a one-sided join; an oracle that replaced it
# with a second too-weak check would be worse than none, because it would look like the
# hole was closed. A guard nobody has watched fail is a guard nobody can trust — the same
# reason test_data_isolation.py carries `test_the_guard_actually_trips`.
def test_the_row_25_guard_rejects_a_one_sided_join() -> None:
    fenced_both_sides = (
        "select ci.entry_date as date, sum(ws.reps * ws.weight_kg) as volume_kg "
        "  from public.workout_sets ws "
        "  join public.check_ins ci on ci.id = ws.check_in_id "
        " where ws.user_id = $1 and ci.user_id = $1 "
        "   and ci.entry_date between $2 and $3 "
        " group by ci.entry_date order by ci.entry_date"
    )
    _assert_owner_scoped_on_both_sides(fenced_both_sides, "workout_sets")  # must NOT raise

    # The leak: `user_id` appears, so test_data_isolation.py's regex is satisfied, but the
    # check-in side of the join is wide open — anyone's check-in can supply the entry_date a
    # row is bucketed under. This is the exact statement row 25 was written to catch.
    leaks_through_the_join = (
        "select ci.entry_date as date, sum(ws.reps * ws.weight_kg) as volume_kg "
        "  from public.workout_sets ws "
        "  join public.check_ins ci on ci.id = ws.check_in_id "
        " where ws.user_id = $1 and ci.entry_date between $2 and $3 "
        " group by ci.entry_date"
    )
    with pytest.raises(AssertionError):
        _assert_owner_scoped_on_both_sides(leaks_through_the_join, "workout_sets")

    # ...and the mirror image: the fact side unfenced. `ci.user_id` alone would let the
    # window pick up every user's sets that happen to hang off the caller's check-ins.
    fact_side_unfenced = (
        "select ci.entry_date as date, sum(ws.reps * ws.weight_kg) as volume_kg "
        "  from public.workout_sets ws "
        "  join public.check_ins ci on ci.id = ws.check_in_id "
        " where ci.user_id = $1 and ci.entry_date between $2 and $3 "
        " group by ci.entry_date"
    )
    with pytest.raises(AssertionError):
        _assert_owner_scoped_on_both_sides(fact_side_unfenced, "workout_sets")

    # A filter that is present but NOT CONTIGUOUS — the shape a loose two-substring check
    # (PR 1's `_assert_owner_scoped_range`) cannot tell from the real thing. Both words are
    # in the statement; neither fences anything.
    non_contiguous = (
        "select ci.entry_date as date, sum(ws.reps * ws.weight_kg) as volume_kg "
        "  from public.workout_sets ws "
        "  join public.check_ins ci on ci.id = ws.check_in_id "
        " where ws.user_id = ci.user_id and ci.entry_date between $2 and $3 "
        " group by ci.entry_date"
    )
    with pytest.raises(AssertionError):
        _assert_owner_scoped_on_both_sides(non_contiguous, "workout_sets")

    # And an unbounded window: both owners fenced, but the range gone. `days` would then be
    # decoration and the only bound on payload size would be the size of the user's history.
    unbounded_window = (
        "select ci.entry_date as date, sum(ws.reps * ws.weight_kg) as volume_kg "
        "  from public.workout_sets ws "
        "  join public.check_ins ci on ci.id = ws.check_in_id "
        " where ws.user_id = $1 and ci.user_id = $1 "
        " group by ci.entry_date"
    )
    with pytest.raises(AssertionError):
        _assert_owner_scoped_on_both_sides(unbounded_window, "workout_sets")

    # Finally: the helper must not depend on the author picking the aliases `ws` / `ci`. The
    # approved row spells them that way, but the security property is "both sides fenced",
    # so an equivalent statement with different aliases — and none at all — still passes.
    other_aliases = (
        "select c.entry_date as date, sum(s.reps * s.weight_kg) as volume_kg "
        "  from public.workout_sets as s "
        "  join public.check_ins as c on c.id = s.check_in_id "
        " where s.user_id = $1 and c.user_id = $1 "
        "   and c.entry_date between $2 and $3 "
        " group by c.entry_date"
    )
    _assert_owner_scoped_on_both_sides(other_aliases, "workout_sets")  # must NOT raise


# AC row 25: EVERY trends statement carries a CONTIGUOUS owner filter on both sides of its
# join — `<fact>.user_id = $1` and `<check_ins>.user_id = $1` — plus the window.
#
# This is the sharpest row in the table and the reason it exists is written out at
# `_assert_owner_scoped_on_both_sides` above: test_data_isolation.py searches the whole
# statement for the word `user_id`, so a join fenced on only one side passes it silently
# while letting anyone's check-in supply the date a row is bucketed under.
async def test_every_trends_statement_is_owner_scoped_on_both_sides_of_its_join(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, MIDDAY_UTC)
    pool = _sign_in("UTC")

    resp = await client.get("/trends?days=30")

    assert resp.status_code == 200
    statements = _trends_statements(pool)
    assert statements, f"no statement touched a fact table; calls were {pool.conn.calls}"

    for query, args in statements:
        touched = [t for t in _FACT_TABLES if f"public.{t}" in _normalize(query)]
        assert len(touched) == 1, (
            f"a trends statement must aggregate exactly one fact table so its owner filter "
            f"is unambiguous; this one touches {touched}: {_normalize(query)}"
        )
        _assert_owner_scoped_on_both_sides(query, touched[0])
        # The owner is the verified caller and the window is the service's, in that order —
        # `$1` has to BE the user id for the contiguous clauses above to mean anything.
        assert args == (USER_ID, date(2026, 7, 3), date(2026, 8, 1)), args


# AC row 25 ("every trends statement" — the completeness half): all four user-owned fact
# tables are actually read. Without this the loop above is vacuously satisfied by an
# implementation that reads three of them and returns [] for the fourth, and "every
# statement is scoped" would be a claim about a set nobody checked the size of.
async def test_all_four_fact_tables_are_read_for_a_trends_window(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, MIDDAY_UTC)
    pool = _sign_in("UTC")

    resp = await client.get("/trends?days=30")

    assert resp.status_code == 200
    read = {
        table
        for query, _ in _trends_statements(pool)
        for table in _FACT_TABLES
        if f"public.{table}" in _normalize(query)
    }
    assert read == set(_FACT_TABLES), f"fact tables never read: {set(_FACT_TABLES) - read}"


# ================================ C. auth (row 26) ================================
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


# AC row 26: no Authorization header -> 401 (and the RFC 6750 challenge header).
async def test_trends_without_token_is_401(client: AsyncClient) -> None:
    _use_fake_jwks()

    resp = await client.get("/trends?days=30")

    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"


# AC row 26: a malformed token -> 401, not a 500.
async def test_trends_with_malformed_token_is_401(client: AsyncClient) -> None:
    _use_fake_jwks()

    resp = await client.get("/trends?days=30", headers={"Authorization": "Bearer not.a.jwt"})

    assert resp.status_code == 401


# AC row 26: a token with an unreadable (non-base64) payload -> 401, not a 500.
async def test_trends_with_garbage_bearer_value_is_401(client: AsyncClient) -> None:
    _use_fake_jwks()
    header = base64.urlsafe_b64encode(json.dumps({"alg": "ES256"}).encode()).rstrip(b"=").decode()

    resp = await client.get(
        "/trends?days=30", headers={"Authorization": f"Bearer {header}.%%%.%%%"}
    )

    assert resp.status_code == 401


# AC row 26: a properly-signed but EXPIRED token -> 401. Everything about it is valid except
# the clock, so this proves expiry is actually checked.
async def test_trends_with_expired_token_is_401(client: AsyncClient) -> None:
    _use_fake_jwks()

    resp = await client.get(
        "/trends?days=30", headers={"Authorization": f"Bearer {_make_token(expires_in=-60)}"}
    )

    assert resp.status_code == 401


# ================= real-DB integration (rows 3, 8-24) =================
#
# Gated exactly like test_check_in_history.py's. These run against LOCAL Supabase and are
# the ONLY place the aggregation rows can be graded: every number in section B is computed
# by SQL, so a fake that hands back a pre-computed row proves nothing about it.

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
        await conn.execute("delete from auth.users where id = any($1::uuid[])", list(user_ids))


async def _add_set(
    pool: Any,
    user_id: uuid.UUID,
    check_in_id: uuid.UUID,
    exercise: str,
    set_number: int,
    reps: int,
    weight_kg: Decimal | None,
) -> uuid.UUID:
    """Attach one workout_sets row to a check-in, straight to SQL.

    A FIXTURE, deliberately not routed through the AI extraction service: going through the
    model would make every aggregation row below depend on extraction behaviour it is not
    about (and would bill Anthropic from CI).

    The name is resolved with `coalesce(canonical_id, id)` — the exact expression
    `app.db.facts.resolve_exercise` uses — because aliases resolve at WRITE time (#19), so
    that is the id a real 'curls' set would already carry. Row 20 depends on this being the
    same resolution and not a second, more forgiving one.
    """
    async with pool.acquire() as conn:
        exercise_id = await conn.fetchval(
            "select coalesce(canonical_id, id) from public.exercises where name = $1", exercise
        )
        assert exercise_id is not None, f"seeded exercise catalog is missing {exercise!r}"
        set_id: uuid.UUID = await conn.fetchval(
            "insert into public.workout_sets"
            " (user_id, check_in_id, exercise_id, set_number, reps, weight_kg)"
            " values ($1, $2, $3, $4, $5, $6) returning id",
            user_id,
            check_in_id,
            exercise_id,
            set_number,
            reps,
            weight_kg,
        )
    return set_id


async def _add_sleep(
    pool: Any,
    user_id: uuid.UUID,
    check_in_id: uuid.UUID,
    hours: Decimal,
    quality: int | None,
    created_at: datetime,
) -> None:
    """One sleep_entries row with an EXPLICIT created_at.

    Explicit because rows 15/16 are decided by `created_at` ordering, and `now()` is the
    transaction timestamp — two rows written close together can tie, which would make those
    tests pass or fail on scheduling rather than on the rule.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into public.sleep_entries (user_id, check_in_id, hours, quality, created_at)"
            " values ($1, $2, $3, $4, $5)",
            user_id,
            check_in_id,
            hours,
            quality,
            created_at,
        )


async def _add_bodyweight(
    pool: Any,
    user_id: uuid.UUID,
    check_in_id: uuid.UUID,
    weight_kg: Decimal,
    created_at: datetime,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into public.bodyweight_entries (user_id, check_in_id, weight_kg, created_at)"
            " values ($1, $2, $3, $4)",
            user_id,
            check_in_id,
            weight_kg,
            created_at,
        )


async def _add_nutrition(
    pool: Any,
    user_id: uuid.UUID,
    check_in_id: uuid.UUID,
    description: str,
    calories: Decimal,
    protein_g: Decimal,
    carbs_g: Decimal,
    fat_g: Decimal,
    created_at: datetime,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into public.nutrition_entries"
            " (user_id, check_in_id, description, calories, protein_g, carbs_g, fat_g, created_at)"
            " values ($1, $2, $3, $4, $5, $6, $7, $8)",
            user_id,
            check_in_id,
            description,
            calories,
            protein_g,
            carbs_g,
            fat_g,
            created_at,
        )


def _at(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=UTC)


# AC row 3 (the behavioural half): a check-in whose entry_date is the user's LOCAL Aug 1 —
# written while the server clock already said Aug 2 UTC — has its facts inside the window.
# The fake tier pins the window's END; this proves the facts on that day are actually
# aggregated rather than sitting one day outside a window computed from the server's date.
@requires_db
async def test_a_check_in_on_the_users_local_date_is_inside_the_window() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool

    a = uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a)
        async with pool.acquire() as conn:
            await conn.execute(
                "update public.profiles set timezone = 'America/Los_Angeles' where id = $1", a
            )
        # The user's local today in LA — the same date POST /check-ins would have stamped.
        la_today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
        row = await insert_check_in(pool, a, "evening squats", la_today)
        await _add_set(pool, a, row["id"], "squat", 1, 8, Decimal("100"))

        # Imported HERE, not at the top of the test, so every fixture above runs
        # against real SQL before the missing module stops us. At oracle time this
        # is the ModuleNotFoundError; the seeding and the raw-SQL inserts are proven.
        from app.services.trends import get_trends

        trends = await get_trends(pool, a, days=30)

        assert trends.end_date == la_today
        assert [p.date for p in trends.volume] == [la_today]
        assert trends.volume[0].volume_kg == Decimal("800")
    finally:
        await _delete_users(pool, a)
        await close_pool(pool)


# AC row 8: facts on two days with nothing between -> the volume series has EXACTLY 2
# entries. Sparse, not padded: 28 null objects is not data, and a padded series is what an
# implementation that walks the calendar produces.
@requires_db
async def test_volume_series_is_sparse_not_padded() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool

    a = uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a)
        today = datetime.now(UTC).date()
        older, newer = today - timedelta(days=4), today
        for day in (older, newer):
            row = await insert_check_in(pool, a, f"squats {day}", day)
            await _add_set(pool, a, row["id"], "squat", 1, 5, Decimal("100"))

        # Imported HERE, not at the top of the test, so every fixture above runs
        # against real SQL before the missing module stops us. At oracle time this
        # is the ModuleNotFoundError; the seeding and the raw-SQL inserts are proven.
        from app.services.trends import get_trends

        trends = await get_trends(pool, a, days=30)

        assert len(trends.volume) == 2, [p.date for p in trends.volume]
        assert [p.date for p in trends.volume] == [older, newer]
    finally:
        await _delete_users(pool, a)
        await close_pool(pool)


# AC row 9: every series comes back OLDEST FIRST — the opposite of /check-ins, on purpose
# (decision 6: a chart reads left-to-right, a log reads newest-first). Exact lists, not
# "contains": a series that happened to include both dates in either order would satisfy a
# membership assertion while rendering the month backwards.
@requires_db
async def test_all_series_are_ordered_oldest_first() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool

    a = uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a)
        today = datetime.now(UTC).date()
        older, newer = today - timedelta(days=4), today
        for index, day in enumerate((older, newer)):
            row = await insert_check_in(pool, a, f"log {day}", day)
            await _add_set(pool, a, row["id"], "squat", 1, 5, Decimal("100"))
            await _add_sleep(pool, a, row["id"], Decimal("7.5"), 4, _at(day, 8))
            await _add_bodyweight(pool, a, row["id"], Decimal(f"8{index}.5"), _at(day, 8))
            await _add_nutrition(
                pool,
                a,
                row["id"],
                "oats",
                Decimal("400"),
                Decimal("20"),
                Decimal("60"),
                Decimal("10"),
                _at(day, 8),
            )

        # Imported HERE, not at the top of the test, so every fixture above runs
        # against real SQL before the missing module stops us. At oracle time this
        # is the ModuleNotFoundError; the seeding and the raw-SQL inserts are proven.
        from app.services.trends import get_trends

        trends = await get_trends(pool, a, days=30)

        assert [p.date for p in trends.volume] == [older, newer]
        assert [p.date for p in trends.sleep] == [older, newer]
        assert [p.date for p in trends.bodyweight] == [older, newer]
        assert [p.date for p in trends.nutrition] == [older, newer]
    finally:
        await _delete_users(pool, a)
        await close_pool(pool)


# AC row 10 (the hand-computed fixture): one day of 4 sets x 8 reps @ 100 kg -> volume_kg
# is exactly 3200. Not "a number appeared" — 4 * 8 * 100, written out.
@requires_db
async def test_one_days_tonnage_is_reps_times_weight_summed() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool

    a = uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a)
        today = datetime.now(UTC).date()
        row = await insert_check_in(pool, a, "4x8 @ 100", today)
        for set_number in range(1, 5):
            await _add_set(pool, a, row["id"], "squat", set_number, 8, Decimal("100"))

        # Imported HERE, not at the top of the test, so every fixture above runs
        # against real SQL before the missing module stops us. At oracle time this
        # is the ModuleNotFoundError; the seeding and the raw-SQL inserts are proven.
        from app.services.trends import get_trends

        trends = await get_trends(pool, a, days=30)

        assert len(trends.volume) == 1
        assert trends.volume[0].date == today
        assert trends.volume[0].volume_kg == Decimal("3200")  # 4 sets * 8 reps * 100 kg
        assert trends.volume[0].bodyweight_sets == 0
        assert trends.volume[0].bodyweight_reps == 0
    finally:
        await _delete_users(pool, a)
        await close_pool(pool)


# AC row 11 (decision 1, and the assertion has to be exact): a day of 3 x 10 pushups with a
# NULL weight has volume_kg **null**, bodyweight_sets 3, bodyweight_reps 30.
#
# `is None`, never `== 0` and never "falsy". The day did not have zero tonnage, it had none
# to measure — and `sum(reps * NULL)` is how a real bodyweight session silently becomes 0 kg
# on a chart that then reads as a wasted day. A test that accepted either value would be
# blind to precisely the bug decision 1 was taken to prevent.
@requires_db
async def test_a_bodyweight_only_day_has_null_tonnage_not_zero() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool

    a = uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a)
        today = datetime.now(UTC).date()
        row = await insert_check_in(pool, a, "3x10 pushups", today)
        for set_number in range(1, 4):
            await _add_set(pool, a, row["id"], "pushups", set_number, 10, None)

        # Imported HERE, not at the top of the test, so every fixture above runs
        # against real SQL before the missing module stops us. At oracle time this
        # is the ModuleNotFoundError; the seeding and the raw-SQL inserts are proven.
        from app.services.trends import get_trends

        trends = await get_trends(pool, a, days=30)

        assert len(trends.volume) == 1
        point = trends.volume[0]
        assert point.volume_kg is None  # NOT 0 — nothing to measure is not zero
        assert point.volume_kg != Decimal("0")
        assert point.bodyweight_sets == 3
        assert point.bodyweight_reps == 30
    finally:
        await _delete_users(pool, a)
        await close_pool(pool)


# AC row 12: that pushups-only day STILL APPEARS in the volume series. The trap in a sparse
# series — a day whose tonnage is null is easy to filter out, and then a week of calisthenics
# renders as a week off. Exactly one entry, on exactly that date.
@requires_db
async def test_a_bodyweight_only_day_still_appears_in_the_volume_series() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool

    a = uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a)
        today = datetime.now(UTC).date()
        weighted_day, bodyweight_day = today - timedelta(days=2), today
        weighted = await insert_check_in(pool, a, "squats", weighted_day)
        await _add_set(pool, a, weighted["id"], "squat", 1, 5, Decimal("100"))
        calisthenics = await insert_check_in(pool, a, "pushups only", bodyweight_day)
        for set_number in range(1, 4):
            await _add_set(pool, a, calisthenics["id"], "pushups", set_number, 10, None)

        # Imported HERE, not at the top of the test, so every fixture above runs
        # against real SQL before the missing module stops us. At oracle time this
        # is the ModuleNotFoundError; the seeding and the raw-SQL inserts are proven.
        from app.services.trends import get_trends

        trends = await get_trends(pool, a, days=30)

        assert [p.date for p in trends.volume] == [weighted_day, bodyweight_day]
    finally:
        await _delete_users(pool, a)
        await close_pool(pool)


# AC row 13 (the mixed day — the common case, not an edge): 3x8 @ 100 kg AND 2x10 pushups on
# one day -> volume_kg 2400, bodyweight_sets 2, bodyweight_reps 20. The unweighted sets must
# contribute to neither the tonnage nor its set count, and the weighted ones to neither
# bodyweight counter.
@requires_db
async def test_a_mixed_day_separates_tonnage_from_bodyweight_work() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool

    a = uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a)
        today = datetime.now(UTC).date()
        row = await insert_check_in(pool, a, "squats then pushups", today)
        for set_number in range(1, 4):
            await _add_set(pool, a, row["id"], "squat", set_number, 8, Decimal("100"))
        for set_number in range(1, 3):
            await _add_set(pool, a, row["id"], "pushups", set_number, 10, None)

        # Imported HERE, not at the top of the test, so every fixture above runs
        # against real SQL before the missing module stops us. At oracle time this
        # is the ModuleNotFoundError; the seeding and the raw-SQL inserts are proven.
        from app.services.trends import get_trends

        trends = await get_trends(pool, a, days=30)

        assert len(trends.volume) == 1
        point = trends.volume[0]
        assert point.volume_kg == Decimal("2400")  # 3 * 8 * 100, the pushups excluded
        assert point.bodyweight_sets == 2
        assert point.bodyweight_reps == 20
    finally:
        await _delete_users(pool, a)
        await close_pool(pool)


# AC row 14: two check-ins on the SAME day, each with sets -> the volume is the SUM across
# both, in ONE series entry. A second session is more work, not a correction — the
# latest-wins rule that governs sleep and bodyweight would report only the second session
# here and silently halve a double day.
@requires_db
async def test_two_check_ins_on_one_day_sum_their_volume() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool

    a = uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a)
        today = datetime.now(UTC).date()
        morning = await insert_check_in(pool, a, "morning squats", today)
        await _add_set(pool, a, morning["id"], "squat", 1, 10, Decimal("100"))  # 1000
        evening = await insert_check_in(pool, a, "evening squats", today)
        await _add_set(pool, a, evening["id"], "squat", 1, 5, Decimal("100"))  # 500

        # Imported HERE, not at the top of the test, so every fixture above runs
        # against real SQL before the missing module stops us. At oracle time this
        # is the ModuleNotFoundError; the seeding and the raw-SQL inserts are proven.
        from app.services.trends import get_trends

        trends = await get_trends(pool, a, days=30)

        assert len(trends.volume) == 1, "one calendar day is one point, however many sessions"
        assert trends.volume[0].volume_kg == Decimal("1500")  # 1000 + 500, not 500
    finally:
        await _delete_users(pool, a)
        await close_pool(pool)


# AC row 15 (decision 5, latest-wins for sleep): two sleep rows on one day, 7h then 8h ->
# 8h, the LATEST by created_at. A second row is a correction, and 7.5 (an average) is the
# "mystery number" the decision rejects. Both wrong answers are asserted against by name.
@requires_db
async def test_two_sleep_rows_on_one_day_keep_the_latest() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool

    a = uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a)
        today = datetime.now(UTC).date()
        row = await insert_check_in(pool, a, "slept badly, actually fine", today)
        await _add_sleep(pool, a, row["id"], Decimal("7"), 2, _at(today, 7))
        await _add_sleep(pool, a, row["id"], Decimal("8"), 4, _at(today, 9))  # the correction

        # Imported HERE, not at the top of the test, so every fixture above runs
        # against real SQL before the missing module stops us. At oracle time this
        # is the ModuleNotFoundError; the seeding and the raw-SQL inserts are proven.
        from app.services.trends import get_trends

        trends = await get_trends(pool, a, days=30)

        assert len(trends.sleep) == 1
        assert trends.sleep[0].hours == Decimal("8")  # not 7 (first), not 15 (sum), not 7.5
        assert trends.sleep[0].quality == 4  # the whole latest ROW wins, not just its hours
    finally:
        await _delete_users(pool, a)
        await close_pool(pool)


# AC row 16 (decision 5, latest-wins for bodyweight): two weigh-ins on one day -> the latest
# by created_at. Same rule as sleep; a sum here would report 160 kg.
@requires_db
async def test_two_bodyweight_rows_on_one_day_keep_the_latest() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool

    a = uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a)
        today = datetime.now(UTC).date()
        row = await insert_check_in(pool, a, "weighed in twice", today)
        await _add_bodyweight(pool, a, row["id"], Decimal("80.5"), _at(today, 7))
        await _add_bodyweight(pool, a, row["id"], Decimal("81.25"), _at(today, 19))

        # Imported HERE, not at the top of the test, so every fixture above runs
        # against real SQL before the missing module stops us. At oracle time this
        # is the ModuleNotFoundError; the seeding and the raw-SQL inserts are proven.
        from app.services.trends import get_trends

        trends = await get_trends(pool, a, days=30)

        assert len(trends.bodyweight) == 1
        assert trends.bodyweight[0].weight_kg == Decimal("81.25")  # not 80.5, not 161.75
    finally:
        await _delete_users(pool, a)
        await close_pool(pool)


# AC row 17 (decision 5, the CORRECTED rule): three nutrition rows on one day, 400/700/900
# calories -> 2000, SUMMED. Three rows a day is the normal shape (breakfast, lunch, dinner,
# often across three check-ins), so latest-wins here would report 900 — a plausible-looking
# number that quietly deletes two meals. 900 is asserted against explicitly.
@requires_db
async def test_three_nutrition_rows_on_one_day_are_summed_not_latest() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool

    a = uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a)
        today = datetime.now(UTC).date()
        meals = [
            ("breakfast", Decimal("400"), Decimal("30"), Decimal("40"), Decimal("10"), 8),
            ("lunch", Decimal("700"), Decimal("50"), Decimal("70"), Decimal("20"), 13),
            ("dinner", Decimal("900"), Decimal("60"), Decimal("90"), Decimal("30"), 19),
        ]
        for description, calories, protein, carbs, fat, hour in meals:
            row = await insert_check_in(pool, a, description, today)
            await _add_nutrition(
                pool, a, row["id"], description, calories, protein, carbs, fat, _at(today, hour)
            )

        # Imported HERE, not at the top of the test, so every fixture above runs
        # against real SQL before the missing module stops us. At oracle time this
        # is the ModuleNotFoundError; the seeding and the raw-SQL inserts are proven.
        from app.services.trends import get_trends

        trends = await get_trends(pool, a, days=30)

        assert len(trends.nutrition) == 1
        point = trends.nutrition[0]
        assert point.calories == Decimal("2000")  # 400 + 700 + 900, NOT 900
        assert point.calories != Decimal("900")
        assert point.protein_g == Decimal("140")  # every macro sums, not just calories
        assert point.carbs_g == Decimal("200")
        assert point.fat_g == Decimal("60")
    finally:
        await _delete_users(pool, a)
        await close_pool(pool)


# AC row 18 (the per-exercise hand-computed fixture): an exercise with 12 sets, 96 reps,
# 9180 kg of volume and a top set of 140 kg comes back as ONE row with exactly those four
# numbers.
#
# The fixture, spread over two days so it also proves the table aggregates across the window
# rather than per check-in:
#   day 1: 8@140 (1120) + 9,9,9,8,8 @100 (43 reps -> 4300)   = 6 sets, 51 reps, 5420 kg
#   day 2: 8@100 (800)  + 8,8,7,7,7 @80  (37 reps -> 2960)   = 6 sets, 45 reps, 3760 kg
#   totals: 12 sets, 96 reps, 9180 kg, heaviest single set 140 kg
@requires_db
async def test_per_exercise_row_reports_sets_reps_volume_and_heaviest() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool

    a = uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a)
        today = datetime.now(UTC).date()
        day_one: list[tuple[int, Decimal]] = [
            (8, Decimal("140")),
            (9, Decimal("100")),
            (9, Decimal("100")),
            (9, Decimal("100")),
            (8, Decimal("100")),
            (8, Decimal("100")),
        ]
        day_two: list[tuple[int, Decimal]] = [
            (8, Decimal("100")),
            (8, Decimal("80")),
            (8, Decimal("80")),
            (7, Decimal("80")),
            (7, Decimal("80")),
            (7, Decimal("80")),
        ]
        for offset, sets in ((3, day_one), (0, day_two)):
            day = today - timedelta(days=offset)
            row = await insert_check_in(pool, a, f"squats {day}", day)
            for set_number, (reps, weight) in enumerate(sets, start=1):
                await _add_set(pool, a, row["id"], "squat", set_number, reps, weight)

        # Imported HERE, not at the top of the test, so every fixture above runs
        # against real SQL before the missing module stops us. At oracle time this
        # is the ModuleNotFoundError; the seeding and the raw-SQL inserts are proven.
        from app.services.trends import get_trends

        trends = await get_trends(pool, a, days=30)

        assert len(trends.exercises) == 1
        summary = trends.exercises[0]
        assert summary.name == "squat"
        assert summary.sets == 12
        assert summary.reps == 96
        assert summary.volume_kg == Decimal("9180")
        assert summary.heaviest_kg == Decimal("140")
    finally:
        await _delete_users(pool, a)
        await close_pool(pool)


# AC row 19: the per-exercise table is ordered by volume desc, bodyweight-only exercises
# LAST, tie-broken by reps then name. Determinism is the point — an unordered `group by`
# makes this test flaky and the screen jittery.
#
# Fixture, chosen so every tie-break rule is exercised by exactly one adjacent pair:
#   squat        1000 kg volume        -> first  (volume desc)
#   bench press   500 kg volume        -> second
#   pushups       bodyweight, 50 reps  -> third  (no volume, so after every weighted row)
#   air squat     bodyweight, 30 reps  -> fourth (fewer reps than pushups)
#   burpee        bodyweight, 30 reps  -> fifth  (ties air squat on reps; 'a' < 'b' by name)
@requires_db
async def test_exercise_table_is_ordered_by_volume_then_reps_then_name() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool

    a = uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a)
        today = datetime.now(UTC).date()
        row = await insert_check_in(pool, a, "everything day", today)
        await _add_set(pool, a, row["id"], "squat", 1, 10, Decimal("100"))  # 1000 kg
        await _add_set(pool, a, row["id"], "bench press", 1, 10, Decimal("50"))  # 500 kg
        await _add_set(pool, a, row["id"], "pushups", 1, 50, None)
        await _add_set(pool, a, row["id"], "air squat", 1, 30, None)
        await _add_set(pool, a, row["id"], "burpee", 1, 30, None)

        # Imported HERE, not at the top of the test, so every fixture above runs
        # against real SQL before the missing module stops us. At oracle time this
        # is the ModuleNotFoundError; the seeding and the raw-SQL inserts are proven.
        from app.services.trends import get_trends

        trends = await get_trends(pool, a, days=30)

        assert [e.name for e in trends.exercises] == [
            "squat",
            "bench press",
            "pushups",
            "air squat",
            "burpee",
        ]
    finally:
        await _delete_users(pool, a)
        await close_pool(pool)


# AC row 20 (REAL DB by necessity): an exercise logged as "curls" reports under its canonical
# name, aggregated with sets logged as "barbell curl" — ONE row, not two.
#
# This row cannot be proven by a fake. Alias resolution is a database-level
# `coalesce(canonical_id, id)` against the seeded catalog (20260716184500_extraction.sql
# maps 'curls' -> 'barbell curl'), and the aggregation that must not split is a SQL
# `group by`. A fake pool would be asserting that a hand-written record says 'barbell curl'.
@requires_db
async def test_an_aliased_exercise_aggregates_under_its_canonical_name() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool

    a = uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a)
        today = datetime.now(UTC).date()
        row = await insert_check_in(pool, a, "curls, then barbell curls", today)
        await _add_set(pool, a, row["id"], "curls", 1, 10, Decimal("20"))  # 200 kg
        await _add_set(pool, a, row["id"], "barbell curl", 2, 10, Decimal("20"))  # 200 kg

        # Imported HERE, not at the top of the test, so every fixture above runs
        # against real SQL before the missing module stops us. At oracle time this
        # is the ModuleNotFoundError; the seeding and the raw-SQL inserts are proven.
        from app.services.trends import get_trends

        trends = await get_trends(pool, a, days=30)

        assert [e.name for e in trends.exercises] == ["barbell curl"]  # one movement, one row
        assert trends.exercises[0].sets == 2
        assert trends.exercises[0].reps == 20
        assert trends.exercises[0].volume_kg == Decimal("400")
    finally:
        await _delete_users(pool, a)
        await close_pool(pool)


# AC row 21 (the one place decision 1 is asymmetric, so it is pinned as a decision rather
# than left to read as a bug): an exercise with BOTH weighted and bodyweight sets counts
# every set and every rep, but its volume and heaviest set come from the weighted ones only.
#
# Fixture: dips — 2 weighted sets of 8 @ 20 kg, plus 1 bodyweight set of 12.
#   sets 3, reps 28, volume_kg 320 (2 * 8 * 20), heaviest_kg 20.
@requires_db
async def test_mixed_exercise_counts_all_sets_but_only_weighted_volume() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool

    a = uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a)
        today = datetime.now(UTC).date()
        row = await insert_check_in(pool, a, "weighted dips then bodyweight", today)
        await _add_set(pool, a, row["id"], "dip", 1, 8, Decimal("20"))
        await _add_set(pool, a, row["id"], "dip", 2, 8, Decimal("20"))
        await _add_set(pool, a, row["id"], "dip", 3, 12, None)

        # Imported HERE, not at the top of the test, so every fixture above runs
        # against real SQL before the missing module stops us. At oracle time this
        # is the ModuleNotFoundError; the seeding and the raw-SQL inserts are proven.
        from app.services.trends import get_trends

        trends = await get_trends(pool, a, days=30)

        assert len(trends.exercises) == 1
        summary = trends.exercises[0]
        assert summary.name == "dip"
        assert summary.sets == 3  # ALL sets, including the unweighted one
        assert summary.reps == 28  # 8 + 8 + 12
        assert summary.volume_kg == Decimal("320")  # weighted only: 2 * 8 * 20
        assert summary.heaviest_kg == Decimal("20")
    finally:
        await _delete_users(pool, a)
        await close_pool(pool)


# AC row 22 (THE MANDATORY CROSS-TENANT ROW, value-shaped): A has 10 000 kg of volume in the
# window, B has 3 200; B's trends report 3200.
#
# Value-shaped rather than id-shaped on purpose. An aggregate leak returns a NUMBER, not an
# id — a `group by` that dropped the owner filter would hand B a total of 13 200 while
# containing none of A's ids, so "none of A's ids appear" cannot catch it. And A's rows are
# unchanged afterwards: B's read must not have moved anything of A's.
@requires_db
async def test_bs_tonnage_contains_nothing_of_as() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool

    a, b = uuid.uuid4(), uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a, b)
        today = datetime.now(UTC).date()
        a_row = await insert_check_in(pool, a, "A trains", today)
        for set_number in range(1, 11):  # 10 sets * 10 reps * 100 kg = 10 000 kg
            await _add_set(pool, a, a_row["id"], "squat", set_number, 10, Decimal("100"))
        b_row = await insert_check_in(pool, b, "B trains", today)
        for set_number in range(1, 5):  # 4 sets * 8 reps * 100 kg = 3 200 kg
            await _add_set(pool, b, b_row["id"], "squat", set_number, 8, Decimal("100"))

        # Imported HERE, not at the top of the test, so every fixture above runs
        # against real SQL before the missing module stops us. At oracle time this
        # is the ModuleNotFoundError; the seeding and the raw-SQL inserts are proven.
        from app.services.trends import get_trends

        b_trends = await get_trends(pool, b, days=30)

        b_total = sum((p.volume_kg or Decimal(0) for p in b_trends.volume), Decimal(0))
        assert b_total == Decimal("3200")  # B's own work, and only B's
        assert b_total != Decimal("13200")  # the exact number an unfiltered group by returns
        assert b_total != Decimal("10000")
        assert [e.volume_kg for e in b_trends.exercises] == [Decimal("3200")]

        # ...and A still reports its own 10 000 kg, untouched by B's read.
        a_trends = await get_trends(pool, a, days=30)
        assert sum((p.volume_kg or Decimal(0) for p in a_trends.volume), Decimal(0)) == Decimal(
            "10000"
        )
    finally:
        await _delete_users(pool, a, b)
        await close_pool(pool)


# AC row 23: A and B both logged sleep, bodyweight and calories in the window -> none of A's
# VALUES appear in any of B's series. The leak is per-series: one correctly filtered query
# does not vouch for the other four, and each of these three series is a separate statement.
# Every fixture value is distinctive so an equality assertion can tell them apart.
@requires_db
async def test_no_series_carries_another_users_values() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool

    a, b = uuid.uuid4(), uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a, b)
        today = datetime.now(UTC).date()
        a_row = await insert_check_in(pool, a, "A's day", today)
        await _add_sleep(pool, a, a_row["id"], Decimal("9.5"), 5, _at(today, 8))
        await _add_bodyweight(pool, a, a_row["id"], Decimal("111.5"), _at(today, 8))
        await _add_nutrition(
            pool,
            a,
            a_row["id"],
            "A's feast",
            Decimal("4321"),
            Decimal("321"),
            Decimal("432"),
            Decimal("123"),
            _at(today, 8),
        )
        b_row = await insert_check_in(pool, b, "B's day", today)
        await _add_sleep(pool, b, b_row["id"], Decimal("6.25"), 2, _at(today, 8))
        await _add_bodyweight(pool, b, b_row["id"], Decimal("70.25"), _at(today, 8))
        await _add_nutrition(
            pool,
            b,
            b_row["id"],
            "B's oats",
            Decimal("1234"),
            Decimal("100"),
            Decimal("150"),
            Decimal("40"),
            _at(today, 8),
        )

        # Imported HERE, not at the top of the test, so every fixture above runs
        # against real SQL before the missing module stops us. At oracle time this
        # is the ModuleNotFoundError; the seeding and the raw-SQL inserts are proven.
        from app.services.trends import get_trends

        trends = await get_trends(pool, b, days=30)

        assert [p.hours for p in trends.sleep] == [Decimal("6.25")]
        assert [p.weight_kg for p in trends.bodyweight] == [Decimal("70.25")]
        assert [p.calories for p in trends.nutrition] == [Decimal("1234")]
        # ...and A's distinctive numbers are nowhere, in any series, in any field.
        assert Decimal("9.5") not in [p.hours for p in trends.sleep]
        assert Decimal("111.5") not in [p.weight_kg for p in trends.bodyweight]
        assert Decimal("4321") not in [p.calories for p in trends.nutrition]
        assert Decimal("321") not in [p.protein_g for p in trends.nutrition]
    finally:
        await _delete_users(pool, a, b)
        await close_pool(pool)


# AC row 24: A trained `back squat` and B never did -> `back squat` is absent from B's
# exercise table. A `group by` that forgets the owner filter shows you someone else's
# movements, which leaks what another person trains even before it leaks their numbers.
@requires_db
async def test_bs_exercise_table_never_names_an_exercise_only_a_trained() -> None:
    from app.db.check_ins import insert_check_in
    from app.db.pool import close_pool, create_pool

    a, b = uuid.uuid4(), uuid.uuid4()
    pool = await create_pool(os.environ["DATABASE_URL"])
    try:
        await _seed_users(pool, a, b)
        today = datetime.now(UTC).date()
        a_row = await insert_check_in(pool, a, "A back squats", today)
        await _add_set(pool, a, a_row["id"], "back squat", 1, 5, Decimal("140"))
        b_row = await insert_check_in(pool, b, "B benches", today)
        await _add_set(pool, b, b_row["id"], "bench press", 1, 5, Decimal("60"))

        # Imported HERE, not at the top of the test, so every fixture above runs
        # against real SQL before the missing module stops us. At oracle time this
        # is the ModuleNotFoundError; the seeding and the raw-SQL inserts are proven.
        from app.services.trends import get_trends

        trends = await get_trends(pool, b, days=30)

        assert [e.name for e in trends.exercises] == ["bench press"]
        assert "back squat" not in [e.name for e in trends.exercises]
    finally:
        await _delete_users(pool, a, b)
        await close_pool(pool)
