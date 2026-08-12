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

=====================================================================================
🔓 AMENDED BY ISSUE #48 — APPROVED IN ADVANCE, IN THE OPEN. THIS IS THE AUDIT TRAIL.
=====================================================================================
#48 drops the `off_topic` label: the gate now answers one question ("is this person in
danger?"), `coach` is everything that is not a crisis, and there is no diversion left but
crisis. That changes three of #21's rows and adds five new ones, all inside the oracle
commit on `fix/48-gate-asks-one-question` and none of it after:

  * DELETED — `test_row10_off_topic_never_calls_sonnet_and_stores_off_topic_reply`. #21 row
    10 no longer exists; there is no `off_topic` label and no `OFF_TOPIC_REPLY`.
  * AMENDED — `test_row14_intent_schema_rejects_an_unknown_label`: the valid set is now
    exactly ("coach", "crisis") and `"off_topic"` moves into the junk list (#48 AC row 8).
  * AMENDED — `test_row14_unknown_label_is_treated_as_a_gate_failure`: parametrised over
    two out-of-range labels, `"off_topic"` among them (#48 AC row 9).
  * AMENDED — `test_row23_every_coach_statement_is_owner_scoped`: three statements become
    two, because `delete_reply_with_content` is gone (#48 AC row 26).
  * ADDED — #48 AC rows 20, 21, 22, 23, 25 (section G at the bottom).

These edits are legitimate ONLY because Toby approved each of them BEFORE any
implementation existed, as rows of the v2 acceptance table on issue #48:
  table:    https://github.com/tobyliu2004/coach-bill/issues/48#issuecomment-5268060575
  approval: https://github.com/tobyliu2004/coach-bill/issues/48#issuecomment-5268138951
Same doctrine as #21 row 32's amendment of #37's frozen oracle (see the audit block at
tests/test_table_privileges.py). The rule that makes an oracle worth anything is that you
do not edit it to make code pass; changing an approved expectation, in the open, against a
row Toby signed off in advance, is the sanctioned path — quietly relaxing an assertion
because the code tripped it is the thing that path exists to prevent.

⚠️ AND THE LESSON THAT COST THE MOST: rows 20-22 below are SUBSTRING TESTS ON PROMPTS. They
are cheap tripwires that catch a deletion. They are NOT proof that a prompt works — #21's
suite was fully green while the gate was filing the most on-topic request the app can
receive as off-topic. The real check on both prompts is the live tier,
tests/test_coach_live_model.py, and it says so in its own docstring.
"""

import inspect
import re
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
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


# 🔓 #21 AC row 10 ("what's the capital of France" -> `off_topic`, exactly OFF_TOPIC_REPLY,
# zero Sonnet calls) WAS TESTED HERE. #48 deletes the row and the test with it: there is no
# `off_topic` label, no `OFF_TOPIC_REPLY`, and trivia now goes to Bill, who answers it like
# a person and steers back. Its replacement is not a unit test at all — it is #48 AC rows 6
# and 16 in the live tier, because "answers like a person" is not something a fake can show.
# Approved in advance as the "what dies" section of the v2 table:
# https://github.com/tobyliu2004/coach-bill/issues/48#issuecomment-5268060575


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


# #48 AC row 8 (amends #21 row 14, schema half): the structured-output shape accepts
# EXACTLY `coach` and `crisis`, and rejects everything else. Untrusted model output that
# didn't validate is a failure, never a default — that doctrine is unchanged; what changed
# is the size of the valid set.
#
# 🔓 `"off_topic"` moved from the accepted list to the junk list. That is the amendment, and
# it is the load-bearing one in the whole ticket: as long as the schema still accepts the
# label, a stale prompt or a cached model behaviour can put the app back where #48 found it.
# Approved in advance as row 8 of the v2 table:
# https://github.com/tobyliu2004/coach-bill/issues/48#issuecomment-5268060575
def test_row14_intent_schema_rejects_an_unknown_label() -> None:
    from pydantic import ValidationError

    from app.ai.gate import Intent

    for label in ("coach", "crisis"):
        assert Intent.model_validate({"label": label}).label == label
    for junk in ("off_topic", "banana", "COACH", "", "fitness"):
        with pytest.raises(ValidationError):
            Intent.model_validate({"label": junk})


# #48 AC row 9 (#21 row 14, dispatch half — unchanged doctrine, wider input): a gate that
# hands back a label outside the TWO is treated as a GATE FAILURE -> 503, nothing stored,
# exactly zero Sonnet calls. The fail-closed `else` branch stays.
#
# `model_construct` bypasses validation on purpose: it is the only way to stand in for "the
# gate produced something outside the labels" at this seam. `"off_topic"` is parametrised
# alongside `"banana"` because after #48 it IS an out-of-range label, and a dispatch that
# still has a branch for it would pass the `"banana"` case while quietly keeping the old
# behaviour alive.
@pytest.mark.parametrize("label", ["banana", "off_topic"])
async def test_row14_unknown_label_is_treated_as_a_gate_failure(
    client: AsyncClient, label: str
) -> None:
    from app.ai.gate import Intent

    gate = _FakeGate(intent=Intent.model_construct(label=label))
    coach = _FakeCoach()
    pool, _g, _c = _sign_in(_Db(check_ins=[_check_in_row()]), gate=gate, coach=coach)

    resp = await client.post(f"/check-ins/{CHECK_IN_ID}/reply")

    assert resp.status_code == 503
    assert len(coach.calls) == 0
    _assert_nothing_stored(pool)


# =====================================================================================
# C. Safety (rows 15 plumbing, 17, 18)
# =====================================================================================


# #48 AC row 14 (= #21 row 15, plumbing half — unchanged, and re-approved verbatim): the
# gate returns `crisis` -> Sonnet is NEVER called (exactly zero), the stored reply is
# EXACTLY CRISIS_REPLY, and there is exactly ONE row. Still true under two labels: crisis is
# now the ONLY diversion, so this is the only short-circuit left in the dispatch.
#
# The single most important row in the table; whether Haiku actually labels that sentence
# `crisis` is the live tier's half (#48 AC row 10 in tests/test_coach_live_model.py), and
# neither half is sufficient alone.
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

    # 🔓 #48 AC row 26, AS CORRECTED AND RE-APPROVED BY TOBY (2026-08-12). The count stays
    # 3. The v2 table originally said "now 2 statements, not 3" — that was a drafting error,
    # raised by test-author before any implementation existed, ruled on in the open, and
    # corrected on the issue rather than patched quietly here:
    # https://github.com/tobyliu2004/coach-bill/issues/48#issuecomment-5268060575
    #
    # WHY THE NUMBER DOESN'T MOVE, which is the interesting part:
    #   - this test calls THREE db/coach.py functions (`get_reply_for_check_in`,
    #     `insert_reply`, `list_replies_for_check_ins`), each issuing one statement against
    #     public.coach_messages.
    #   - it never called `delete_reply_with_content`. That function arrived in PR #47's
    #     review round, AFTER #21's oracle froze, and nobody added it here — so #48 deletes
    #     a statement this test was never counting, and 3 stays 3.
    #
    # ⚠️ THAT GAP IS A REAL WEAKNESS IN THIS GUARD, FILED SEPARATELY. The count exists to
    # notice a NEW unfenced statement — but it only ever sees statements issued by functions
    # this test remembers to call. A whole db function with a DELETE in it slipped past it
    # once already. Do not read a green here as "every statement in db/coach.py is fenced";
    # read it as "every statement these three functions issue is fenced".
    #
    # The ASSERTION ITSELF IS UNCHANGED AND UNWEAKENED: a real number, never loosened to
    # `>=`, and every counted statement still has to carry a contiguous `user_id = $N` bound
    # to the caller. This row is an INVARIANT #48 must not break, not a behaviour it changes,
    # so it is green before the build and must stay green after it.
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


# =====================================================================================
# G. Issue #48 — the prompts, and the removals (rows 20, 21, 22, 23, 25)
# =====================================================================================
#
# Written before any implementation change existed, from the v2 table approved on
# 2026-08-12: https://github.com/tobyliu2004/coach-bill/issues/48#issuecomment-5268138951
#
# ⚠️ ROWS 20-22 ARE SUBSTRING TESTS, AND THE APPROVED TABLE CALLS THEM "cheap tripwires,
# not proof" IN SO MANY WORDS. They catch a deletion — someone dropping the crisis block or
# leaving `off_topic` behind. They CANNOT catch a prompt that contains all the right words
# and still misclassifies, which is precisely what shipped in #21. The real check on both
# prompts is tests/test_coach_live_model.py, run by hand before any prompt change ships.

_BACKEND_APP = Path(__file__).resolve().parents[1] / "app"
_FRONTEND_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"


def _source_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    """Every source file under `root`, ignoring caches and build output."""
    ignored = {"__pycache__", "node_modules", "dist", ".venv"}
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.suffix in suffixes and not ignored & set(path.parts)
    ]


def _joined_source(path: Path) -> str:
    """A file's text with adjacent string literals joined and whitespace collapsed.

    SQL in this codebase is written as implicitly-concatenated literals across source
    lines, so a statement can be split anywhere — including in the middle of `delete from
    public.coach_messages`. Row 25 asks whether the STATEMENT exists, not whether a line
    does, so the search runs over the joined text as well as the raw one.
    """
    text = path.read_text(encoding="utf-8")
    joined = re.sub(r"[\"']\s*[\"']", "", text)
    return " ".join(joined.lower().split())


# #48 AC row 20: GATE_SYSTEM_PROMPT describes what the app IS; defines `coach` as
# everything that is not a crisis; keeps the "genuine ambiguity -> crisis" line; contains no
# `off_topic` and no enumerated off-topic list.
#
# The prompt-shape bug that caused #48 was a prompt that ENUMERATED examples instead of
# describing the app, so the last two assertions are the ones with teeth here: a re-added
# list of what counts as out of scope recreates the bug exactly.
def test_i48_row20_gate_prompt_describes_the_app_and_defines_coach_by_principle() -> None:
    from app.ai.gate import GATE_SYSTEM_PROMPT

    lowered = GATE_SYSTEM_PROMPT.lower()

    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n", lowered) if s.strip()]

    # (a) It says what the app IS. A classifier that has never been told what it is
    # classifying FOR can only pattern-match on the examples it was given.
    #
    # Asserted as a CONJUNCTION INSIDE ONE SENTENCE — the app named, and what a person does
    # with it — because either half alone is satisfied by the prompt that shipped the bug
    # (it says "the app" in passing, and it says "training" in an example list). A
    # description is a sentence, not two words in the same document.
    app_markers = ("coach bill", "the app", "fitness app", "training app", "coaching app")
    usage_markers = ("check-in", "check in", "checks in", "logs", "training")
    described = [
        s
        for s in sentences
        if any(a in s for a in app_markers) and any(u in s for u in usage_markers)
    ]
    assert described, (
        f"GATE_SYSTEM_PROMPT never describes the app: no single sentence pairs one of "
        f"{app_markers} with one of {usage_markers}. Describing what the app IS, instead of "
        "enumerating what falls outside it, is the whole fix in #48."
    )

    # (b) `coach` is defined as EVERYTHING THAT IS NOT A CRISIS — a principle, not a list.
    # This one only bites together with (d): the old prompt also said "coach — everything
    # else", but "else" meant "neither crisis NOR off_topic", and off_topic was a co-equal
    # bucket you could fall into. With two labels and no enumerated list, "everything else"
    # IS the principle. (d) is what makes that true; this keeps the sentence there.
    principle_markers = (
        "everything that is not a crisis",
        "anything that is not a crisis",
        "everything that isn't a crisis",
        "anything that isn't a crisis",
        "not a crisis",
        "everything else",
        "anything else",
    )
    assert any(marker in lowered for marker in principle_markers), (
        f"GATE_SYSTEM_PROMPT does not define `coach` as everything that is not a crisis "
        f"(expected one of {principle_markers}); a definition by example is what filed "
        "'plan my next month and put it in the dashboard' as out of scope"
    )

    # (c) The "genuine ambiguity -> crisis" line survives, and resolves to CRISIS. Asserted
    # per sentence rather than per prompt: `crisis` appears all over this prompt, so
    # "ambiguity is mentioned AND crisis is mentioned" would pass on a prompt that says
    # "when unsure, choose coach". Safety wins the tie-break — that was judgment call #1.
    ambiguity_markers = ("ambigu", "unsure", "in doubt", "not sure", "uncertain", "borderline")
    ambiguous_lines = [s for s in sentences if any(m in s for m in ambiguity_markers)]
    assert ambiguous_lines, (
        f"GATE_SYSTEM_PROMPT has no tie-break line at all (expected one of "
        f"{ambiguity_markers}); the gate must be told what to do when it genuinely can't "
        "tell, and that answer is `crisis`"
    )
    assert any("crisis" in line for line in ambiguous_lines), (
        "the tie-break line does not resolve to `crisis`: "
        f"{ambiguous_lines}. Genuine crisis-vs-coach ambiguity resolves to crisis — the one "
        "label that exists for safety does not lose a coin flip."
    )

    # (d) No `off_topic`, and no enumerated off-topic list. The enumerated phrases are the
    # ones named in the issue as what the model pattern-matched against.
    assert "off_topic" not in lowered, (
        "GATE_SYSTEM_PROMPT still mentions `off_topic`; the label does not exist after #48"
    )
    enumerations = ("write code", "write emails", "code or emails", "essay", "trivia")
    for phrase in enumerations:
        assert phrase not in lowered, (
            f"GATE_SYSTEM_PROMPT enumerates an out-of-scope example ({phrase!r}) — an "
            "example list is the shape of prompt that caused this bug, and 'plan it all "
            "out … put it in the dashboard' pattern-matched straight onto this one"
        )


# #48 AC row 21: COACH_SYSTEM_PROMPT contains a description of the app (what Bill can and
# cannot do) AND still contains the crisis block with all three resources verbatim and the
# not-medical-advice block.
#
# The three resources are asserted as EXACT strings, not as properties, because that is what
# the row says and because a hotline number is the one piece of copy where a paraphrase is a
# defect. (#21 AC row 17's currency warning applies: a number that has been discontinued is
# a dead end that still reads like help. Re-verify against the operators' own pages whenever
# this list or CRISIS_REPLY is touched.)
def test_i48_row21_coach_prompt_describes_the_app_and_keeps_both_safety_blocks() -> None:
    from app.ai.coach import COACH_SYSTEM_PROMPT

    lowered = COACH_SYSTEM_PROMPT.lower()

    # (a) What the app is, and what Bill can and cannot do. Without this, "can you put my
    # plan in the dashboard" has no honest answer available to the model (#48 AC rows 4/18).
    #
    # The dashboard is named specifically, and that is a deliberate choice worth defending:
    # it is the write-surface the reported bug turned on, and rows 4 and 18 both use it as
    # THE case. A prompt that never mentions it cannot ground row 18's "says plainly it
    # can't put anything in the dashboard". If a future prompt describes the app well in
    # other words, that is a correctness-table conversation, not a marker edit.
    assert "dashboard" in lowered, (
        "COACH_SYSTEM_PROMPT never mentions the dashboard — the one app surface the user "
        "asked Bill to write to (#48 AC rows 4 and 18). Bill cannot answer honestly about "
        "a thing he was never told exists; he guesses, which is what #48 is fixing."
    )

    # ...and it says what Bill CANNOT do, IN THE SAME SENTENCE as something about the app.
    # Asserted per sentence on purpose: this prompt is full of ordinary negations ("do not
    # pretend to see history you don't have"), so a prompt-wide `any("don't have")` passes
    # on the prompt that shipped the bug. What has to be new is a stated limit ABOUT THE APP.
    limit_markers = (
        "can't",
        "cannot",
        "can not",
        "unable to",
        "no way to",
        "don't have the ability",
        "never claim",
        "do not claim",
    )
    app_words = ("dashboard", "app", "plan", "program", "save", "add", "create", "write")
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n", lowered) if s.strip()]
    limit_lines = [
        s
        for s in sentences
        if any(m in s for m in limit_markers) and any(w in s for w in app_words)
    ]
    assert limit_lines, (
        "COACH_SYSTEM_PROMPT never states a limit ABOUT THE APP — no sentence pairs one of "
        f"{limit_markers} with one of {app_words}. A coach who doesn't know his own limits "
        "invents them, and the invented answer here is 'sure, I've added it to your "
        "dashboard'."
    )

    # (b) The crisis block, with all three resources verbatim. After #48 this is the LAST
    # line of defence, not belt-and-braces: crisis is the only diversion left, so a gate
    # false negative means Sonnet answers a crisis message and this block is what it has.
    for resource in ("988", "888-375-7767", "findahelpline.com"):
        assert resource in COACH_SYSTEM_PROMPT, (
            f"COACH_SYSTEM_PROMPT no longer names {resource!r}; all three resources are "
            "required verbatim, and the behaviour behind this substring is #48 AC row 15 "
            "in tests/test_coach_live_model.py"
        )

    # (c) The not-medical-advice block (same marker list as #21 AC row 18).
    medical_markers = (
        "medical advice",
        "not a doctor",
        "not a medical",
        "medical professional",
        "healthcare professional",
        "diagnos",
    )
    assert any(marker in lowered for marker in medical_markers), (
        f"COACH_SYSTEM_PROMPT has no not-medical-advice instruction (expected one of "
        f"{medical_markers})"
    )


# #48 AC row 22: the string `off_topic` / `OFF_TOPIC` appears NOWHERE in backend/app/ or
# frontend/src/. The label, the constant, the mirrored copy, the service branch, the type
# union — all of it. A leftover branch is a live path back to the bug.
def test_i48_row22_the_off_topic_string_is_gone_from_the_whole_app() -> None:
    files = [
        *_source_files(_BACKEND_APP, (".py",)),
        *_source_files(_FRONTEND_SRC, (".ts", ".tsx")),
    ]
    # The scan must actually have scanned something — an empty glob would make this test
    # pass by finding nothing, which is the vacuous-green failure mode of every file walk.
    assert len(files) > 20, f"the source scan found only {len(files)} files; the paths are wrong"

    offenders: list[str] = []
    for path in files:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "off_topic" in line.lower():
                offenders.append(f"{path}:{lineno}: {line.strip()}")

    assert offenders == [], (
        "`off_topic` still exists in shipped source after #48 removed the label:\n"
        + "\n".join(offenders)
    )


# #48 AC row 23: `DELETE /check-ins/{id}/reply` does not exist -> 405, not 404.
#
# 405 SPECIFICALLY, and the difference is the entire test. A 404 is what the route returns
# TODAY for a reply it won't retract, so a 404 here would be indistinguishable between "the
# route is gone" and "the route is alive and declined". Only 405 (Starlette's answer when
# the path exists for another method) proves the endpoint itself was removed.
async def test_i48_row23_the_retract_endpoint_no_longer_exists(client: AsyncClient) -> None:
    pool, gate, coach = _sign_in(_Db(check_ins=[_check_in_row()]))

    resp = await client.delete(f"/check-ins/{CHECK_IN_ID}/reply")

    assert resp.status_code == 405, (
        "DELETE /check-ins/{id}/reply must not exist after #48 — with no off-topic label "
        "there is nothing to retract, and 'give me a different answer' is a request to "
        "spend money again (that is #26's per-user caps, not a button)"
    )
    assert pool.conn.calls == []  # a route that doesn't exist reaches no database
    assert gate.calls == []
    assert coach.calls == []
    _assert_nothing_stored(pool)


# #48 AC row 25: backend/app/ contains NO `delete from public.coach_messages` statement
# anywhere.
#
# This replaces #47's `test_a_crisis_reply_can_never_be_retracted`, and it is STRONGER: that
# test proved one caller refused one deletion, which leaves the deleting statement sitting
# in db/ for the next caller to find. This proves the statement does not exist at all. A
# crisis reply is now un-re-rollable BY CONSTRUCTION — do not re-introduce a general
# "regenerate this reply" without re-deciding that safety question.
def test_i48_row25_no_statement_anywhere_deletes_a_coach_message() -> None:
    pattern = re.compile(r"delete\s+from\s+(?:public\.)?coach_messages")

    files = _source_files(_BACKEND_APP, (".py",))
    assert len(files) > 10, f"the source scan found only {len(files)} files; the path is wrong"

    offenders: list[str] = []
    for path in files:
        if pattern.search(_joined_source(path)):
            offenders.append(str(path))

    assert offenders == [], (
        "a statement that deletes coach_messages rows still exists in backend/app/:\n"
        + "\n".join(offenders)
        + "\nThe grant is revoked in the same PR (#48 AC row 24), so this statement could "
        "only fail at runtime — but the safety property is that no code can ask."
    )
