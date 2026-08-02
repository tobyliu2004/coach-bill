"""Oracle suite for issue #21 — Coach Bill replies (Sonnet coach + Haiku intent gate).

This file is part of commit #1 on `feat/21-coach-replies`, written BEFORE any
implementation exists. It encodes the 42-row correctness table Toby approved on the issue
on 2026-08-02 — and nothing else. At oracle time `from app.ai.gate import get_gate`,
`from app.ai.coach import get_coach` and `from app.services.coach import build_context` all
raise ImportError: that is the CORRECT failure.

Tiers, and which rows live where:

1. THIS FILE — fake pool + fake `IntentGate`/`Coach` injected through their FastAPI deps,
   plus pure-function tests. Runs in CI, gates the merge. Rows 1, 2, 3, 4, 7 (route half),
   8, 9, 10, 13, 14, 15 (plumbing half), 17, 18, 21, 23, 24 (constant), 25, 26,
   27 (pure + constant halves), 30 (batched-read half), 33, 34, 35, 36, 37.
2. tests/test_coach_db.py — the real-DB tier (RLS_DATABASE_URL). Rows 5, 6, 7 (the guard),
   22, 24, 27, 28, 29, 30, 31. A fake cannot execute `where ... and user_id = $1`, so it
   cannot prove cross-tenant isolation; those rows are only real there.
3. tests/test_coach_live_model.py — the gated live-model tier (LIVE_MODEL_TESTS=1, never in
   CI). Rows 11, 12, 15 (classification half), 16, 19, 20 — the only real check on the two
   prompts.
4. frontend/src/lib/coachView.test.ts — rows 38-41.

Rows this file deliberately does NOT claim to cover:
  - Row 32 (table privileges) amends the frozen `C3_APP_VERBS` oracle in
    tests/test_table_privileges.py. That amendment is Toby's own one-line commit against
    the approved row; duplicating it here would give the row two oracles that can drift.
  - Row 34's DETECTION half ("Sonnet hit max_tokens") happens inside `SonnetCoach` against
    the Anthropic SDK's `stop_reason`. At the `Coach` protocol seam a truncated reply is a
    string like any other, so this tier can only pin the CONSEQUENCE — a coach that fails
    never becomes a stored reply. Testing the detection would need a mocked vendor SDK,
    which this project does not do anywhere. Flagged rather than faked.
  - Row 36's end-to-end "the vendor hangs" cannot be run without hanging the suite. What is
    asserted instead: the real clients carry a bounded (finite) client timeout, at the 25s
    the row's approved note fixes, and a timeout-shaped failure surfaces as 503 with nothing
    stored.

The imports of the not-yet-existing modules are LAZY (inside helpers and test bodies), the
way tests/test_table_privileges.py does it, so each row fails on its own with a clear
ImportError instead of one collection error hiding thirty rows.

Every test names the AC row it covers.
"""

import inspect
import re
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from app.auth import get_current_user_id
from app.main import app

USER_ID = uuid.uuid4()
OTHER_USER_ID = uuid.uuid4()
CHECK_IN_ID = uuid.uuid4()
SECOND_CHECK_IN_ID = uuid.uuid4()
MISSING_CHECK_IN_ID = uuid.uuid4()
CREATED_AT = datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC)
ENTRY_DATE = date(2026, 8, 2)

CHECK_IN_TEXT = "bench 135 4x8, slept 6h"
COACH_TEXT = "Nice work on the bench — that's a solid volume day."

# The four user-owned fact tables. Named here so the fake can answer their reads with `[]`
# and so row 37 can assert nothing in this feature ever writes to them.
_FACT_TABLES = ("workout_sets", "nutrition_entries", "sleep_entries", "bodyweight_entries")


# =====================================================================================
# SQL helpers
# =====================================================================================


def _normalize(query: str) -> str:
    """Lowercase, collapse whitespace, standardise spacing around `=`.

    Statements are built by concatenating adjacent string literals, so a clause can be
    split across Python source lines; normalising is what makes "contiguous" mean
    contiguous in the SQL rather than contiguous in the file. Copied from tests/test_trends.py
    — deliberately a FILE-LOCAL copy, per #45's precedent, so this suite's security guard
    cannot be weakened from a distance.
    """
    collapsed = " ".join(query.lower().split())
    return re.sub(r"\s*=\s*", " = ", collapsed)


def _is_identity_statement(query: str) -> bool:
    """`authed_conn`'s two per-transaction identity statements, which are not app queries."""
    q = _normalize(query)
    return "set_config" in q or q.startswith("set local role")


def _assert_owner_filter(query: str, args: tuple[Any, ...], user_id: uuid.UUID) -> None:
    """AC row 23: this statement names `user_id = $N` as a CONTIGUOUS clause, and `$N` IS
    the verified caller.

    Both halves matter. `test_data_isolation.py` only checks that the *word* `user_id`
    appears somewhere in the statement, which a `select id, user_id, content ... where
    check_in_id = $1` passes silently while filtering on nothing. And a contiguous
    `user_id = $2` bound to something that isn't the caller fences nothing either — so the
    parameter is resolved against the arguments actually bound.

    Self-tested against deliberately-wrong statements below, so it is a guard we have
    watched fail rather than one we hope works.
    """
    q = _normalize(query)
    positions = re.findall(r"user_id = \$(\d+)", q)
    assert positions, (
        f"no CONTIGUOUS `user_id = $N` clause in this statement — the owner filter is the "
        f"first lock and must be in the same statement (backend.md rule 2): {q}"
    )
    bound = [args[int(p) - 1] for p in positions if int(p) - 1 < len(args)]
    assert user_id in bound, (
        f"the statement filters on `user_id = ${positions}` but that parameter is bound to "
        f"{bound!r}, not the verified caller {user_id!r}: {q}"
    )


def _exists_guard(query: str) -> str | None:
    """The body of the `where exists (...)` parent-ownership guard, if there is one."""
    match = re.search(r"exists\s*\((.*?)\)", _normalize(query))
    return match.group(1) if match else None


# =====================================================================================
# The fake database
# =====================================================================================
#
# Routed BY STATEMENT SHAPE rather than by position (the shape of `_FakeConn` in
# test_check_ins.py / test_check_in_history.py). Deliberate, and the reason is what this
# file is: there is no implementation yet, so the exact ORDER and COUNT of the reads
# `reply_to_check_in` makes is not something the approved table fixes — only that the right
# rows come back and that every statement is owner-scoped. A positional fake would encode
# an ordering nobody approved and go red later for a reason that has nothing to do with the
# rows, and "the test looks wrong" is a correctness-table conversation, not a quiet patch.
#
# It is not looser where it counts: every (query, args) is still recorded in order in
# `pool.conn.calls`, so every assertion about WHAT SQL RAN (row 23's owner filter, row 1's
# single insert, rows 13/33/35's "nothing stored", row 37's untouched tables) is exactly as
# strong as a positional fake's would be. It is also stateful for `coach_messages`, which is
# what makes row 2's second request a real get-or-create rather than a primed answer.


class _Db:
    """The rows this request's database contains, and what its writes do."""

    def __init__(
        self,
        *,
        check_ins: list[dict[str, Any]] | None = None,
        replies: dict[uuid.UUID, dict[str, Any]] | None = None,
        tz: str | None = "UTC",
        weight_unit: str = "lb",
        goal: str | None = None,
        insert_blocked: bool = False,
    ) -> None:
        self.check_ins: dict[uuid.UUID, dict[str, Any]] = {
            row["id"]: row for row in (check_ins if check_ins is not None else [_check_in_row()])
        }
        # keyed by check_in_id — one reply per check-in is exactly what row 2 makes true
        self.replies: dict[uuid.UUID, dict[str, Any]] = dict(replies or {})
        self.tz = tz
        self.weight_unit = weight_unit
        self.goal = goal
        # Row 7: the check-in vanished between the ownership read and the insert, so the
        # rule-4 `where exists` guard matches nothing and RETURNING yields no row.
        self.insert_blocked = insert_blocked
        self.inserted: list[dict[str, Any]] = []

    def _insert_reply(self, args: tuple[Any, ...]) -> dict[str, Any] | None:
        if self.insert_blocked:
            return None
        content = next((a for a in args if isinstance(a, str)), "")
        check_in_id = next((a for a in args[1:] if isinstance(a, uuid.UUID)), None)
        row: dict[str, Any] = {
            "id": uuid.uuid4(),
            "user_id": args[0] if args else None,
            "check_in_id": check_in_id,
            "role": "assistant",
            "content": content,
            "created_at": CREATED_AT,
        }
        if check_in_id is not None:
            self.replies[check_in_id] = row
        self.inserted.append(row)
        return row

    def route(self, method: str, query: str, args: tuple[Any, ...]) -> Any:
        q = _normalize(query)

        if "insert into public.coach_messages" in q:
            return self._insert_reply(args)

        # Fact-table reads (the bundled facts read and every trends aggregate) — empty here;
        # what they MEAN is #19's and #20's oracle, not this ticket's.
        if any(f"public.{table}" in q for table in _FACT_TABLES):
            return []

        if "public.coach_messages" in q:
            if method == "fetch":  # the batched read (rows 27, 30)
                return list(self.replies.values())
            found = next((a for a in args if isinstance(a, uuid.UUID) and a in self.replies), None)
            return self.replies.get(found) if found is not None else None

        if "public.profiles" in q:
            if method == "fetchval":
                head = q.split(" from ")[0]
                if "timezone" in head:
                    return self.tz
                if "weight_unit" in head:
                    return self.weight_unit
                return None
            return {
                "id": USER_ID,
                "display_name": None,
                "weight_unit": self.weight_unit,
                "goal": self.goal,
                "timezone": self.tz,
                "consented_at": CREATED_AT,
                "created_at": CREATED_AT,
            }

        if "public.check_ins" in q:
            if method == "fetch":
                return list(self.check_ins.values())
            found = next(
                (a for a in args if isinstance(a, uuid.UUID) and a in self.check_ins), None
            )
            return self.check_ins.get(found) if found is not None else None

        return [] if method == "fetch" else None


class _FakeTxn:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FakeConn:
    def __init__(self, db: _Db) -> None:
        self.db = db
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.identity_calls: list[tuple[str, tuple[Any, ...]]] = []

    def transaction(self) -> _FakeTxn:
        return _FakeTxn()

    def _record(self, query: str, args: tuple[Any, ...]) -> None:
        # Identity SQL is `authed_conn`'s, not the app's; it lands in its own list so
        # `calls` holds ONLY real queries and "nothing stored" assertions stay exact.
        # Routed by CONTENT, not by method, so a write issued through `execute` is still
        # visible to those assertions.
        if _is_identity_statement(query):
            self.identity_calls.append((query, args))
        else:
            self.calls.append((query, args))

    async def execute(self, query: str, *args: Any) -> str:
        self._record(query, args)
        if not _is_identity_statement(query):
            self.db.route("execute", query, args)
        return "OK"

    async def fetchval(self, query: str, *args: Any) -> Any:
        self._record(query, args)
        answer = self.db.route("fetchval", query, args)
        if isinstance(answer, dict):  # a RETURNING id on an insert
            return answer.get("id")
        if isinstance(answer, list):
            return None
        return answer

    async def fetchrow(self, query: str, *args: Any) -> Any:
        self._record(query, args)
        return self.db.route("fetchrow", query, args)

    async def fetch(self, query: str, *args: Any) -> Any:
        answer = self.db.route("fetch", query, args)
        self._record(query, args)
        return answer if isinstance(answer, list) else []


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class FakePool:
    def __init__(self, db: _Db) -> None:
        self.conn = _FakeConn(db)

    def acquire(self) -> _FakeAcquire:
        # One conn across acquires, so every statement of the request accumulates in order.
        return _FakeAcquire(self.conn)


# =====================================================================================
# The fake gate and coach
# =====================================================================================


class _FakeGate:
    """Records every classification asked for; returns a primed `Intent` or raises.

    `calls` is the whole point of rows 6/10/13/15: "the model was never called" is a count
    of exactly 0, not a reply that merely looks short.
    """

    def __init__(self, intent: Any = None, error: BaseException | None = None) -> None:
        self._intent = intent
        self._error = error
        self.calls: list[str] = []

    async def classify(self, text: str) -> Any:
        self.calls.append(text)
        if self._error is not None:
            raise self._error
        return self._intent


class _FakeCoach:
    """Records (context, text) for every call; returns a primed reply or raises."""

    def __init__(self, text: str = COACH_TEXT, error: BaseException | None = None) -> None:
        self._text = text
        self._error = error
        self.calls: list[tuple[str, str]] = []

    async def reply(self, context: str, text: str) -> str:
        self.calls.append((context, text))
        if self._error is not None:
            raise self._error
        return self._text


def _intent(label: str) -> Any:
    """A VALIDATED `Intent`. Lazy import: at oracle time this raises ImportError."""
    from app.ai.gate import Intent

    return Intent.model_validate({"label": label})


def _check_in_row(**overrides: Any) -> dict[str, Any]:
    """A `check_ins` row as asyncpg would return it."""
    row: dict[str, Any] = {
        "id": CHECK_IN_ID,
        "raw_text": CHECK_IN_TEXT,
        "source": "text",
        "entry_date": ENTRY_DATE,
        "created_at": CREATED_AT,
        "extraction_status": "done",
    }
    row.update(overrides)
    return row


def _reply_row(check_in_id: uuid.UUID, content: str, **overrides: Any) -> dict[str, Any]:
    """A stored `coach_messages` row as asyncpg would return it."""
    row: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": USER_ID,
        "check_in_id": check_in_id,
        "role": "assistant",
        "content": content,
        "created_at": CREATED_AT,
    }
    row.update(overrides)
    return row


def _sign_in(
    db: _Db | None = None,
    *,
    gate: _FakeGate | None = None,
    coach: _FakeCoach | None = None,
    authenticated: bool = True,
) -> tuple[FakePool, _FakeGate, _FakeCoach]:
    """Wire the app: verified caller = USER_ID, fake pool, fake gate, fake coach.

    The gate/coach overrides are what keep CI off the network — without them the route
    would build the real `HaikuGate`/`SonnetCoach` and dial Anthropic. Lazy imports, so a
    missing module fails THIS test rather than the whole file's collection.
    """
    from app.ai.coach import get_coach
    from app.ai.gate import get_gate
    from app.deps import get_pool

    pool = FakePool(db if db is not None else _Db())
    the_gate = gate if gate is not None else _FakeGate(intent=_intent("coach"))
    the_coach = coach if coach is not None else _FakeCoach()
    if authenticated:
        app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[get_gate] = lambda: the_gate
    app.dependency_overrides[get_coach] = lambda: the_coach
    return pool, the_gate, the_coach


def _inserts(pool: FakePool) -> list[tuple[str, tuple[Any, ...]]]:
    """Every statement that wrote a `coach_messages` row."""
    return [
        (q, a) for q, a in pool.conn.calls if "insert into public.coach_messages" in _normalize(q)
    ]


def _assert_nothing_stored(pool: FakePool) -> None:
    """No reply was written — the assertion behind rows 13/33/34/35/36."""
    assert _inserts(pool) == [], f"a reply was stored on a failed request: {_inserts(pool)}"
    assert pool.conn.db.inserted == [], pool.conn.db.inserted


def _assert_check_ins_and_facts_untouched(pool: FakePool) -> None:
    """AC row 37: this feature never writes to `check_ins` or the four fact tables.

    #18/#19's write path must not be reachable from this feature's failures — or its
    successes. Asserted over every statement the request issued, not just the failing one.
    """
    protected = ("check_ins", *_FACT_TABLES)
    for query, _args in pool.conn.calls:
        q = _normalize(query)
        is_write = q.startswith("insert") or q.startswith("update") or q.startswith("delete")
        if not is_write:
            continue
        for table in protected:
            assert f"public.{table}" not in q.split("where exists")[0], (
                f"this feature wrote to public.{table}, which it must never do (row 37): {q}"
            )


# =====================================================================================
# A. Endpoint contract & ownership (rows 1, 2, 3, 4, 7, 8)
# =====================================================================================


# AC row 1: POST /check-ins/{id}/reply, valid token, own check-in, no reply yet -> 201, body
# is exactly {id, content, created_at}, and ONE row lands in coach_messages.
async def test_row1_first_reply_is_201_with_the_reply_body(client: AsyncClient) -> None:
    pool, gate, coach = _sign_in(_Db(check_ins=[_check_in_row()]))

    resp = await client.post(f"/check-ins/{CHECK_IN_ID}/reply")

    assert resp.status_code == 201
    body = resp.json()
    assert set(body) == {"id", "content", "created_at"}  # CoachReplyOut, nothing else
    assert body["content"] == COACH_TEXT
    assert uuid.UUID(body["id"])  # a real id, not an echo of the check-in id
    assert body["id"] != str(CHECK_IN_ID)
    assert len(_inserts(pool)) == 1  # exactly one row stored
    assert len(coach.calls) == 1
    assert len(gate.calls) == 1
    _assert_check_ins_and_facts_untouched(pool)


# AC row 2: the same call again -> 200 (not 201), the SAME id and content, NO second model
# call of either kind, and still exactly one row. Idempotent get-or-create: a double-click
# or a remount must not spend Sonnet twice or store a second reply.
async def test_row2_second_request_is_200_and_spends_no_model_call(client: AsyncClient) -> None:
    pool, gate, coach = _sign_in(_Db(check_ins=[_check_in_row()]))

    first = await client.post(f"/check-ins/{CHECK_IN_ID}/reply")
    second = await client.post(f"/check-ins/{CHECK_IN_ID}/reply")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["content"] == first.json()["content"]
    assert len(_inserts(pool)) == 1  # still ONE row, not two
    # Haiku is a model call too — the get-or-create short-circuits before EITHER model.
    assert len(gate.calls) == 1
    assert len(coach.calls) == 1


# AC row 3: no Authorization header -> 401, nothing stored. (The full auth negative set —
# expired, wrong signature, algorithm confusion, wrong issuer, wrong audience — is covered
# against the real dependency in tests/test_auth.py; this pins that the new endpoint is
# actually behind it.)
async def test_row3_without_token_is_401_and_stores_nothing(client: AsyncClient) -> None:
    pool, gate, coach = _sign_in(_Db(check_ins=[_check_in_row()]), authenticated=False)

    resp = await client.post(f"/check-ins/{CHECK_IN_ID}/reply")

    assert resp.status_code == 401
    assert pool.conn.calls == []  # never reached the database
    assert gate.calls == []
    assert coach.calls == []
    _assert_nothing_stored(pool)


# AC row 4: a check_in_id that doesn't exist -> 404, nothing stored. The 404 must come from
# the row being absent, not from the route being absent: assert the database was actually
# asked. (Someone else's id is also a 404 — that is row 5, and only a real database can
# prove it; see tests/test_coach_db.py.)
async def test_row4_unknown_check_in_id_is_404(client: AsyncClient) -> None:
    pool, _gate, _coach = _sign_in(_Db(check_ins=[_check_in_row()]))

    resp = await client.post(f"/check-ins/{MISSING_CHECK_IN_ID}/reply")

    assert resp.status_code == 404
    assert pool.conn.calls, "the 404 must mean 'no such row', not 'no such route'"
    _assert_nothing_stored(pool)


# AC row 7: the check-in is deleted between the ownership read and the insert. The rule-4
# `where exists (... and user_id = $1)` guard then matches nothing, RETURNING yields no row,
# and the endpoint is a 404 with no orphan reply. The earlier read is convenience; this is
# the guarantee. (The guard's SQL behaviour is proven against a real database in
# tests/test_coach_db.py — a fake cannot execute it.)
async def test_row7_insert_blocked_by_the_guard_is_404_with_no_orphan(client: AsyncClient) -> None:
    pool, _gate, coach = _sign_in(_Db(check_ins=[_check_in_row()], insert_blocked=True))

    resp = await client.post(f"/check-ins/{CHECK_IN_ID}/reply")

    assert resp.status_code == 404
    assert len(coach.calls) == 1  # the read succeeded; the WRITE is what lost the race
    assert pool.conn.db.replies == {}  # no orphan reply row
    _assert_check_ins_and_facts_untouched(pool)


# AC row 8: a check_in_id that isn't a UUID -> 422 (path validation), no db call, no model call.
async def test_row8_non_uuid_check_in_id_is_422(client: AsyncClient) -> None:
    pool, gate, coach = _sign_in()

    resp = await client.post("/check-ins/not-a-uuid/reply")

    assert resp.status_code == 422
    assert pool.conn.calls == []
    assert gate.calls == []
    assert coach.calls == []


# =====================================================================================
# B. The intent gate (rows 9, 10, 13, 14)
# =====================================================================================


# AC row 9: "bench 135 4x8, slept 6h" -> the gate returns `coach`, and Sonnet IS called,
# once, with the check-in's own text. (Whether Haiku actually returns `coach` for that
# sentence is the live tier's job — tests/test_coach_live_model.py.)
async def test_row9_coach_label_calls_sonnet_with_the_check_in_text(client: AsyncClient) -> None:
    gate = _FakeGate(intent=_intent("coach"))
    coach = _FakeCoach()
    pool, _g, _c = _sign_in(
        _Db(check_ins=[_check_in_row(raw_text=CHECK_IN_TEXT)]), gate=gate, coach=coach
    )

    resp = await client.post(f"/check-ins/{CHECK_IN_ID}/reply")

    assert resp.status_code == 201
    assert gate.calls == [CHECK_IN_TEXT]  # the gate saw the check-in text
    assert len(coach.calls) == 1
    _context, text = coach.calls[0]
    assert text == CHECK_IN_TEXT
    assert resp.json()["content"] == COACH_TEXT
    assert len(_inserts(pool)) == 1


# AC row 10: "what's the capital of France" -> `off_topic`; Sonnet is NEVER called (a count
# of exactly 0 — a short-looking reply proves nothing); the stored reply is EXACTLY
# OFF_TOPIC_REPLY.
async def test_row10_off_topic_never_calls_sonnet_and_stores_off_topic_reply(
    client: AsyncClient,
) -> None:
    from app.ai.coach import OFF_TOPIC_REPLY

    text = "what's the capital of France"
    gate = _FakeGate(intent=_intent("off_topic"))
    coach = _FakeCoach()
    pool, _g, _c = _sign_in(_Db(check_ins=[_check_in_row(raw_text=text)]), gate=gate, coach=coach)

    resp = await client.post(f"/check-ins/{CHECK_IN_ID}/reply")

    assert resp.status_code == 201
    assert coach.calls == []  # exactly zero Sonnet calls — the cost control itself
    assert resp.json()["content"] == OFF_TOPIC_REPLY
    inserts = _inserts(pool)
    assert len(inserts) == 1
    assert OFF_TOPIC_REPLY in inserts[0][1]  # stored verbatim, not paraphrased


# AC row 13: the gate raises (vendor down / timeout) -> 503, nothing stored, no Sonnet call.
# FAILS CLOSED, deliberately the opposite of the extraction path: failing open would send
# unclassified text to the coach and defeat the safety control this gate exists to be.
async def test_row13_gate_failure_is_503_and_fails_closed(client: AsyncClient) -> None:
    gate = _FakeGate(error=RuntimeError("anthropic is down"))
    coach = _FakeCoach()
    pool, _g, _c = _sign_in(_Db(check_ins=[_check_in_row()]), gate=gate, coach=coach)

    resp = await client.post(f"/check-ins/{CHECK_IN_ID}/reply")

    assert resp.status_code == 503
    assert coach.calls == []  # unclassified text NEVER reaches the coach
    _assert_nothing_stored(pool)
    _assert_check_ins_and_facts_untouched(pool)


# AC row 14 (schema half): the structured-output shape REJECTS a label outside the three.
# Untrusted model output that didn't validate is a failure, never a default.
def test_row14_intent_schema_rejects_an_unknown_label() -> None:
    from pydantic import ValidationError

    from app.ai.gate import Intent

    for label in ("coach", "crisis", "off_topic"):
        assert Intent.model_validate({"label": label}).label == label
    for junk in ("banana", "COACH", "", "fitness"):
        with pytest.raises(ValidationError):
            Intent.model_validate({"label": junk})


# AC row 14 (dispatch half): a gate that hands back a label outside the three is treated as
# a GATE FAILURE -> 503, nothing stored, no Sonnet call. `model_construct` bypasses
# validation on purpose: it is the only way to stand in for "the gate produced something
# outside the three labels" at this seam, and the point of the row is that the dispatch
# must never quietly default such a value to `coach` (or to `off_topic`).
async def test_row14_unknown_label_is_treated_as_a_gate_failure(client: AsyncClient) -> None:
    from app.ai.gate import Intent

    gate = _FakeGate(intent=Intent.model_construct(label="banana"))
    coach = _FakeCoach()
    pool, _g, _c = _sign_in(_Db(check_ins=[_check_in_row()]), gate=gate, coach=coach)

    resp = await client.post(f"/check-ins/{CHECK_IN_ID}/reply")

    assert resp.status_code == 503
    assert coach.calls == []
    _assert_nothing_stored(pool)


# =====================================================================================
# C. Safety (rows 15 plumbing, 17, 18)
# =====================================================================================


# AC row 15 (plumbing half): the gate returns `crisis` -> Sonnet is NEVER called (exactly
# zero), and the stored reply is EXACTLY CRISIS_REPLY. The single most important row in the
# table; whether Haiku actually labels the row-15 sentence `crisis` is the live tier's half
# (tests/test_coach_live_model.py), and neither half is sufficient alone.
async def test_row15_crisis_never_calls_sonnet_and_stores_crisis_reply(
    client: AsyncClient,
) -> None:
    from app.ai.coach import CRISIS_REPLY

    text = "I haven't eaten in three days and I don't see the point anymore"
    gate = _FakeGate(intent=_intent("crisis"))
    coach = _FakeCoach()
    pool, _g, _c = _sign_in(_Db(check_ins=[_check_in_row(raw_text=text)]), gate=gate, coach=coach)

    resp = await client.post(f"/check-ins/{CHECK_IN_ID}/reply")

    assert resp.status_code == 201
    assert coach.calls == []  # exactly zero — no coaching is generated over a crisis
    assert resp.json()["content"] == CRISIS_REPLY
    inserts = _inserts(pool)
    assert len(inserts) == 1
    assert CRISIS_REPLY in inserts[0][1]  # stored verbatim


# AC row 17: CRISIS_REPLY names at least one real, current crisis resource; does NOT
# diagnose; gives NO training or nutrition advice.
#
# Asserted as PROPERTIES, never as a literal string. Toby approves the exact wording
# separately (⚖️ on the row), and an assertion on the literal text would make this oracle a
# photograph of a string the test author picked. If approved copy ever trips one of these
# lists, that is a correctness-table conversation — not a quiet edit to the list.
def test_row17_crisis_reply_names_a_resource_and_gives_no_advice() -> None:
    from app.ai.coach import CRISIS_REPLY

    assert CRISIS_REPLY.strip(), "CRISIS_REPLY must not be empty"

    # At least one REAL, current crisis resource. A reply with warm words and no way to
    # reach help is the failure this half exists to catch.
    # ⚠️ CURRENCY IS PART OF THE ASSERTION. A helpline that has been shut down is not a
    # "real, current crisis resource" — it is a dead end that still reads like help, which
    # is the failure this row exists to catch. Verified against the operators' own pages on
    # 2026-08-02; re-verify whenever this list or CRISIS_REPLY is touched, and at launch
    # (#26). The pre-2023 NEDA Helpline number (1-800-931-2237) is deliberately NOT here:
    # it was discontinued, and listing it would let dead copy pass this test.
    resources = (
        "988",  # 988 Suicide & Crisis Lifeline — call or text, 24/7
        "741741",  # Crisis Text Line — text HOME
        "888-375-7767",  # ANAD Eating Disorders Helpline (the one CRISIS_REPLY names)
        "findahelpline.com",  # international directory
        "https://",  # any linked service
    )
    assert any(marker in CRISIS_REPLY for marker in resources), (
        f"CRISIS_REPLY names no reachable crisis resource; expected one of {resources}"
    )

    # Does not DIAGNOSE. Naming a condition in the name of a helpline is fine; telling the
    # user they have one is not, so the patterns are the "you have X" shapes only.
    lowered = CRISIS_REPLY.lower()
    diagnoses = (
        "you have",
        "you may have",
        "you might have",
        "you likely have",
        "you're suffering from",
        "you are suffering from",
        "sounds like you have",
        "diagnos",
    )
    for phrase in diagnoses:
        assert phrase not in lowered, f"CRISIS_REPLY diagnoses the user ({phrase!r})"

    # No training or nutrition advice. Nouns that only appear when coaching is happening.
    coaching_nouns = (
        "reps",
        "sets",
        "macros",
        "calories",
        "protein",
        "carbs",
        "deficit",
        "surplus",
        "bulking",
        "cutting",
    )
    for noun in coaching_nouns:
        assert not re.search(rf"\b{noun}\b", lowered), (
            f"CRISIS_REPLY gives training/nutrition advice ({noun!r})"
        )


# AC row 18: the coach system prompt carries explicit crisis AND not-medical-advice
# instructions EVEN THOUGH the gate exists. Second lock — the same doctrine as RLS sitting
# behind the `user_id` filter: neither lock may be the only one.
def test_row18_coach_system_prompt_has_crisis_and_not_medical_instructions() -> None:
    from app.ai.coach import COACH_SYSTEM_PROMPT

    lowered = COACH_SYSTEM_PROMPT.lower()

    crisis_markers = ("crisis", "self-harm", "self harm", "suicid", "harm themselves")
    assert any(m in lowered for m in crisis_markers), (
        "COACH_SYSTEM_PROMPT has no crisis instruction; the gate is not allowed to be the "
        f"only lock (expected one of {crisis_markers})"
    )

    medical_markers = (
        "medical advice",
        "not a doctor",
        "not a medical",
        "medical professional",
        "healthcare professional",
        "diagnos",
    )
    assert any(m in lowered for m in medical_markers), (
        "COACH_SYSTEM_PROMPT has no not-medical-advice instruction; Bill's behaviour must "
        f"match the disclaimer stamped at onboarding (expected one of {medical_markers})"
    )


# =====================================================================================
# D. Context assembly (rows 21, 23, 24 constant, 25, 26, 27 pure + constant)
# =====================================================================================


def _empty_trends() -> Any:
    from app.schemas.trends import TrendsOut

    return TrendsOut(start_date=date(2024, 1, 1), end_date=date(2024, 1, 14))


def _check_in_out(**overrides: Any) -> Any:
    from app.schemas.check_ins import CheckInOut

    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "raw_text": "bench 135 4x8",
        "source": "text",
        "entry_date": date(2024, 1, 14),
        "created_at": datetime(2024, 1, 14, 12, 0, tzinfo=UTC),
        "extraction_status": "done",
    }
    fields.update(overrides)
    return CheckInOut(**fields)


# AC row 21 (purity, half 1): same inputs -> byte-identical string, twice.
def test_row21_build_context_is_deterministic() -> None:
    from app.services.coach import build_context

    kwargs: dict[str, Any] = {
        "goal": "cut to 175",
        "weight_unit": "lb",
        "trends": _empty_trends(),
        "check_ins": [_check_in_out()],
        "recent_replies": ["yesterday's reply"],
    }
    assert build_context(**kwargs) == build_context(**kwargs)


# AC row 21 (purity, half 2): it can't touch a database, structurally — it is a SYNC
# function whose parameters are exactly the five data inputs, keyword-only. No pool, no
# conn, no user_id to look anything up with.
def test_row21_build_context_takes_no_pool_and_is_not_async() -> None:
    from app.services.coach import build_context

    assert not inspect.iscoroutinefunction(build_context)
    params = inspect.signature(build_context).parameters
    assert set(params) == {"goal", "weight_unit", "trends", "check_ins", "recent_replies"}
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params.values())


# AC row 21 (purity, half 3): it reads no clock. Every input is dated 2024, so today's date
# can only appear in the output if the function went and asked what time it is — which is
# what would make rows 22-25 unassertable.
def test_row21_build_context_reads_no_clock() -> None:
    from app.services.coach import build_context

    context = build_context(
        goal=None,
        weight_unit="lb",
        trends=_empty_trends(),
        check_ins=[_check_in_out()],
        recent_replies=[],
    )

    today = datetime.now(UTC).date()
    for stamp in (today.isoformat(), today.strftime("%b %d, %Y"), today.strftime("%B %d, %Y")):
        assert stamp not in context, f"build_context read the clock: {stamp!r} appears in it"


# AC row 23: EVERY statement in db/coach.py names `user_id = $N` as a CONTIGUOUS clause with
# `$N` bound to the verified caller. `db/coach.py` is single-table, so there is no join to
# get half-right — assert it anyway; a guard you only write where you already know it fails
# is not a guard.
async def test_row23_every_coach_statement_is_owner_scoped() -> None:
    from app.db.coach import get_reply_for_check_in, insert_reply, list_replies_for_check_ins

    pool = FakePool(_Db(check_ins=[_check_in_row()]))

    await get_reply_for_check_in(pool, USER_ID, CHECK_IN_ID)  # type: ignore[arg-type]
    await insert_reply(pool, USER_ID, CHECK_IN_ID, COACH_TEXT)  # type: ignore[arg-type]
    await list_replies_for_check_ins(  # type: ignore[arg-type]
        pool, USER_ID, [CHECK_IN_ID, SECOND_CHECK_IN_ID]
    )

    statements = [(q, a) for q, a in pool.conn.calls if "public.coach_messages" in _normalize(q)]
    assert len(statements) == 3, (
        f"expected one statement per db/coach.py function; got {len(statements)}: "
        f"{[_normalize(q) for q, _ in statements]}"
    )
    for query, args in statements:
        _assert_owner_filter(query, args, USER_ID)


# AC row 23 (the guard is one we have watched fail): the assertion above must reject the
# statements a weaker tripwire accepts. `test_data_isolation.py` searches the whole statement
# for the WORD `user_id`, so the first two below pass it silently while fencing nothing.
def test_row23_owner_filter_guard_rejects_unfenced_statements() -> None:
    caller = USER_ID

    # The word `user_id` is right there in the select list — and the WHERE fences nothing.
    with pytest.raises(AssertionError):
        _assert_owner_filter(
            "select id, user_id, content from public.coach_messages where check_in_id = $1",
            (CHECK_IN_ID,),
            caller,
        )
    # Present but not contiguous: both words in the statement, neither a filter.
    with pytest.raises(AssertionError):
        _assert_owner_filter(
            "select id from public.coach_messages c join public.check_ins ci "
            "on ci.id = c.check_in_id where c.user_id = ci.user_id and c.check_in_id = $1",
            (CHECK_IN_ID,),
            caller,
        )
    # Contiguous, parameterised — and bound to somebody else. A filter on the wrong user is
    # not a filter.
    with pytest.raises(AssertionError):
        _assert_owner_filter(
            "select id from public.coach_messages where check_in_id = $1 and user_id = $2",
            (CHECK_IN_ID, OTHER_USER_ID),
            caller,
        )
    # ...and the real thing must NOT raise, in either parameter order.
    _assert_owner_filter(
        "select id, content, created_at from public.coach_messages "
        "where check_in_id = $2 and user_id = $1",
        (caller, CHECK_IN_ID),
        caller,
    )
    _assert_owner_filter(
        "select id, content, created_at, check_in_id from public.coach_messages "
        "where check_in_id = any($1) and user_id = $2",
        ([CHECK_IN_ID], caller),
        caller,
    )


# AC row 7 (the write's own lock, statement half): `insert_reply` proves the parent is the
# caller's INSIDE the write — backend.md rule 4 — rather than trusting the earlier read.
async def test_row7_insert_statement_carries_the_parent_ownership_guard() -> None:
    from app.db.coach import insert_reply

    pool = FakePool(_Db(check_ins=[_check_in_row()]))
    await insert_reply(pool, USER_ID, CHECK_IN_ID, COACH_TEXT)  # type: ignore[arg-type]

    inserts = _inserts(pool)
    assert len(inserts) == 1
    query, args = inserts[0]
    guard = _exists_guard(query)
    assert guard is not None, f"no `where exists (...)` parent guard in the insert: {query}"
    assert "public.check_ins" in guard, guard
    assert "user_id = $1" in guard, f"the guard doesn't fence the parent's owner: {guard}"
    assert "id = $2" in guard, f"the guard doesn't name the parent check-in: {guard}"
    # rule 3: the owner comes from UserIdDep, and the parent id from the path — in that order.
    assert args[0] == USER_ID
    assert args[1] == CHECK_IN_ID


# AC row 24 (the constant): the context window is 14 days. The behavioural half — a user
# with 60 days of history sees exactly the last 14, and day 15 is absent — needs real rows
# and lives in tests/test_coach_db.py.
def test_row24_context_days_is_fourteen() -> None:
    from app.services.coach import CONTEXT_DAYS

    assert CONTEXT_DAYS == 14


# AC row 25: a user with zero prior check-ins builds a context without error, and that
# context invents no history. Asserted COMPARATIVELY rather than against a fixed format: the
# facts of a real check-in appear when there is one and cannot appear when there isn't.
def test_row25_first_ever_check_in_builds_a_context_that_invents_nothing() -> None:
    from app.services.coach import build_context

    empty = build_context(
        goal=None, weight_unit="lb", trends=_empty_trends(), check_ins=[], recent_replies=[]
    )
    with_history = build_context(
        goal=None,
        weight_unit="lb",
        trends=_empty_trends(),
        check_ins=[_check_in_out(raw_text="squat 315 5x3 and slept 9h")],
        recent_replies=[],
    )

    assert isinstance(empty, str)
    assert "squat" in with_history and "315" in with_history  # the history is really in there
    assert "squat" not in empty and "315" not in empty  # ...and nothing is invented without it


# AC row 26: a check-in whose extraction FAILED still gets a reply, built from the raw text.
# Facts are derived; text is the source of truth, so Bill must not go silent because Haiku
# had a bad day.
async def test_row26_failed_extraction_still_gets_a_reply_from_the_raw_text(
    client: AsyncClient,
) -> None:
    text = "did some heavy pulls, felt beat up"
    coach = _FakeCoach()
    pool, _g, _c = _sign_in(
        _Db(check_ins=[_check_in_row(raw_text=text, extraction_status="failed")]), coach=coach
    )

    resp = await client.post(f"/check-ins/{CHECK_IN_ID}/reply")

    assert resp.status_code == 201
    assert len(coach.calls) == 1
    _context, sent_text = coach.calls[0]
    assert sent_text == text  # the raw text, verbatim
    assert len(_inserts(pool)) == 1


# AC row 27 (the constant + the pure half): the last 3 assistant replies ride along in the
# context, so Bill doesn't repeat himself verbatim day to day. Which 3 the service picks out
# of a real history is the behavioural half, in tests/test_coach_db.py.
def test_row27_recent_replies_constant_is_three_and_they_reach_the_context() -> None:
    from app.services.coach import RECENT_REPLIES, build_context

    assert RECENT_REPLIES == 3

    replies = ["reply about squats", "reply about sleep", "reply about protein"]
    context = build_context(
        goal=None,
        weight_unit="lb",
        trends=_empty_trends(),
        check_ins=[_check_in_out()],
        recent_replies=replies,
    )
    for reply in replies:
        assert reply in context, f"a prior reply was dropped from the context: {reply!r}"


# =====================================================================================
# E. Storage — the batched-read half of row 30
# =====================================================================================


# AC row 30: GET /check-ins?days=30 carries each check-in's stored reply (or null), in ONE
# batched read — not one query per check-in. Without this a reply vanishes on refresh; with
# an N+1 it comes back at the cost of a query per row. Whether the reply that comes back is
# the one actually stored is proven against a real database in tests/test_coach_db.py.
async def test_row30_list_bundles_replies_in_one_batched_read(client: AsyncClient) -> None:
    stored = _reply_row(CHECK_IN_ID, COACH_TEXT)
    pool, _g, _c = _sign_in(
        _Db(
            check_ins=[_check_in_row(id=CHECK_IN_ID), _check_in_row(id=SECOND_CHECK_IN_ID)],
            replies={CHECK_IN_ID: stored},
        )
    )

    resp = await client.get("/check-ins?days=30")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    by_id = {row["id"]: row for row in body}
    assert by_id[str(CHECK_IN_ID)]["reply"]["content"] == COACH_TEXT
    assert by_id[str(CHECK_IN_ID)]["reply"]["id"] == str(stored["id"])
    assert by_id[str(SECOND_CHECK_IN_ID)]["reply"] is None  # null, not missing, not an error

    reads = [
        (q, a)
        for q, a in pool.conn.calls
        if "public.coach_messages" in _normalize(q) and _normalize(q).startswith("select")
    ]
    assert len(reads) == 1, f"the reply read must be batched, not one per check-in: {reads}"
    query, args = reads[0]
    _assert_owner_filter(query, args, USER_ID)
    ids = next((a for a in args if isinstance(a, list)), None)
    assert ids is not None, f"the batched read must take the ids as one list: {args!r}"
    assert set(ids) == {CHECK_IN_ID, SECOND_CHECK_IN_ID}


# =====================================================================================
# F. Failure modes (rows 33, 34, 35, 36, 37)
# =====================================================================================


# AC row 33: Sonnet raises -> 503, NOTHING stored, and a retry later returns a real reply.
# The retry half is the point: a half-written or cached failure would be permanently wrong
# through row 2's idempotency.
async def test_row33_coach_failure_is_503_and_a_retry_still_works(client: AsyncClient) -> None:
    db = _Db(check_ins=[_check_in_row()])
    failing = _FakeCoach(error=RuntimeError("anthropic 529"))
    pool, _g, _c = _sign_in(db, coach=failing)

    failed = await client.post(f"/check-ins/{CHECK_IN_ID}/reply")

    assert failed.status_code == 503
    _assert_nothing_stored(pool)
    _assert_check_ins_and_facts_untouched(pool)

    # Same database, a working coach: the retry produces a real reply and stores exactly one.
    working = _FakeCoach(text="Good session — keep the bar path tight.")
    pool2, _g2, _c2 = _sign_in(db, coach=working)
    retried = await client.post(f"/check-ins/{CHECK_IN_ID}/reply")

    assert retried.status_code == 201
    assert retried.json()["content"] == "Good session — keep the bar path tight."
    assert len(_inserts(pool2)) == 1


# AC row 34: Sonnet hits max_tokens mid-sentence -> treated as a FAILURE: 503, nothing
# stored. A truncated reply stored as final would be permanently wrong via row 2.
#
# HONEST SCOPE: the detection ("was this response truncated?") lives inside `SonnetCoach`,
# against the SDK's `stop_reason`, and at the `Coach` seam a truncated string is just a
# string — so this tier pins the CONSEQUENCE (`SonnetCoach` must raise, and a raise must
# never become a stored reply) and not the detection. Testing the detection would require
# mocking the Anthropic SDK, which this project does not do anywhere; flagged, not faked.
async def test_row34_truncated_response_is_a_failure_not_a_stored_reply(
    client: AsyncClient,
) -> None:
    class _Truncated(RuntimeError):
        """What SonnetCoach must raise when stop_reason == 'max_tokens'."""

    coach = _FakeCoach(error=_Truncated("stop_reason=max_tokens"))
    pool, _g, _c = _sign_in(_Db(check_ins=[_check_in_row()]), coach=coach)

    resp = await client.post(f"/check-ins/{CHECK_IN_ID}/reply")

    assert resp.status_code == 503
    _assert_nothing_stored(pool)


# AC row 35: Sonnet returns empty / unparsable content -> failure, 503, nothing stored. Same
# doctrine as extractor.py: untrusted output that didn't validate is a failure, never an
# empty success.
@pytest.mark.parametrize("empty_reply", ["", "   ", "\n\t "])
async def test_row35_empty_reply_is_503_and_stores_nothing(
    client: AsyncClient, empty_reply: str
) -> None:
    coach = _FakeCoach(text=empty_reply)
    pool, _g, _c = _sign_in(_Db(check_ins=[_check_in_row()]), coach=coach)

    resp = await client.post(f"/check-ins/{CHECK_IN_ID}/reply")

    assert resp.status_code == 503
    _assert_nothing_stored(pool)


# AC row 36 (the failure half): a hang surfaces from the SDK as a timeout exception, and
# that must be a 503 with nothing stored — never a 500 and never a partial write.
async def test_row36_timeout_is_503_and_stores_nothing(client: AsyncClient) -> None:
    coach = _FakeCoach(error=TimeoutError("request timed out"))
    pool, _g, _c = _sign_in(_Db(check_ins=[_check_in_row()]), coach=coach)

    resp = await client.post(f"/check-ins/{CHECK_IN_ID}/reply")

    assert resp.status_code == 503
    _assert_nothing_stored(pool)


# AC row 36 (the bound itself): the real clients carry a BOUNDED wall-clock timeout — 25s
# for the coach, the number the approved row fixes — so "the vendor hangs" cannot mean "this
# request hangs". Read off the constructed client rather than a private constant, so the
# assertion is about what the SDK was actually configured with.
#
# What this does NOT prove: that the SDK honours its own timeout. Running a real hang would
# hang the suite; no tier of this project can assert that, and inventing an asyncio.wait_for
# in the service would be inventing a requirement the table doesn't state.
def test_row36_real_clients_have_a_bounded_timeout() -> None:
    from anthropic import AsyncAnthropic

    from app.ai.coach import SonnetCoach
    from app.ai.gate import HaikuGate

    def client_of(obj: object) -> AsyncAnthropic:
        found = [v for v in vars(obj).values() if isinstance(v, AsyncAnthropic)]
        assert len(found) == 1, f"expected exactly one Anthropic client on {obj!r}: {found!r}"
        return found[0]

    coach_timeout = client_of(SonnetCoach()).timeout
    assert coach_timeout == 25.0, f"the approved bound is a 25s client timeout, got {coach_timeout}"

    gate_timeout = client_of(HaikuGate()).timeout
    assert gate_timeout is not None, "the gate's client must have a finite timeout too"
    assert isinstance(gate_timeout, float)
    assert 0 < gate_timeout <= 25.0


# AC row 37: after ANY of the failures above, the check-in row and its facts are untouched.
# The write path shipped in #18/#19 must not be reachable from this feature's failures.
@pytest.mark.parametrize(
    "gate_error,coach_error,coach_text",
    [
        (RuntimeError("gate down"), None, COACH_TEXT),  # row 13
        (None, RuntimeError("sonnet down"), COACH_TEXT),  # row 33
        (None, TimeoutError("hang"), COACH_TEXT),  # row 36
        (None, None, ""),  # row 35
    ],
)
async def test_row37_failures_never_touch_the_check_in_or_its_facts(
    client: AsyncClient,
    gate_error: BaseException | None,
    coach_error: BaseException | None,
    coach_text: str,
) -> None:
    gate = _FakeGate(intent=_intent("coach"), error=gate_error)
    coach = _FakeCoach(text=coach_text, error=coach_error)
    pool, _g, _c = _sign_in(_Db(check_ins=[_check_in_row()]), gate=gate, coach=coach)

    resp = await client.post(f"/check-ins/{CHECK_IN_ID}/reply")

    assert resp.status_code == 503
    _assert_nothing_stored(pool)
    _assert_check_ins_and_facts_untouched(pool)
