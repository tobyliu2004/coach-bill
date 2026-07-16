"""Oracle suite for issue #19 — AI extraction: raw check-in text -> structured facts.

This file is commit #1 on `feat/19-ai-extraction`, written BEFORE any implementation
exists. It encodes section A (rows 1-8) and section B (rows 9-13) of the 23-row
correctness table Toby approved on the issue on 2026-07-16 — and nothing else. At oracle
time `from app.ai.extractor import get_extractor` and `from app.schemas.extraction import
ExtractedFacts` both raise ImportError: that is the CORRECT failure.

Tier: fake `Extractor` injected through `ExtractorDep` + a fake pool. These prove OUR
mapping and persistence (unit conversion, exercise resolution, status decision, what SQL
runs with what arguments). They run in CI and gate the merge. They deliberately do NOT
prove anything the database enforces — grants, RLS, the `resolve_exercise` guard's own
normalization, or "one exercises row". Those live in test_extraction_db.py (rows 14-16),
and whether Haiku understands English lives in test_extraction_live_model.py (rows 21-23).

Imports of the not-yet-existing modules are LAZY (inside helpers), so each test fails on
its own with a clear ImportError instead of the whole file erroring at collection — which
would also swallow the gated skips in the sibling files.

Every test names the AC row it covers.
"""

import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from app.auth import get_current_user_id
from app.main import app

USER_ID = uuid.uuid4()
CHECK_IN_ID = uuid.uuid4()
SECOND_CHECK_IN_ID = uuid.uuid4()
CREATED_AT = datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)

_STATUSES = ("pending", "done", "partial", "failed")

# lb -> kg, rounded to 3 decimals (Toby's "Rounding" decision):
#   135 lb -> 61.235 kg (round-trips to 135.0) · 180 lb -> 81.647 kg
BENCH_135_LB_IN_KG = Decimal("61.235")
BODYWEIGHT_180_LB_IN_KG = Decimal("81.647")


# =====================================================================================
# The fake `public.resolve_exercise` guard
# =====================================================================================
#
# The real guard is a `security definer` SQL function the migration adds; a fake pool
# cannot run SQL, so this mirrors its CONTRACT (normalize: lower/btrim/collapse
# whitespace; reject anything not letters/spaces/hyphens or outside length 1-64; NULL on
# reject) and hands back a stable uuid per normalized name. That is enough to test what
# these rows are actually about — that the app routes every name through the guard and
# honours a NULL. The guard's own normalization is proven against the real DB (row 7/13
# in test_extraction_db.py), because a fake proving its own fake would be a false green.

_EXERCISE_IDS: dict[str, uuid.UUID] = {}


def _fake_resolve_exercise(raw_name: str) -> uuid.UUID | None:
    normalized = re.sub(r"\s+", " ", raw_name.strip().lower())
    if not re.fullmatch(r"[a-z -]{1,64}", normalized):
        return None  # rejected: not letters/spaces/hyphens, or out of length range
    return _EXERCISE_IDS.setdefault(normalized, uuid.uuid4())


def exercise_id(raw_name: str) -> uuid.UUID:
    """The uuid the fake guard will hand back for `raw_name` (asserted on by the tests)."""
    resolved = _fake_resolve_exercise(raw_name)
    assert resolved is not None, f"test bug: {raw_name!r} is not a resolvable name"
    return resolved


# =====================================================================================
# The fake pool — a ROUTER, not a fixed response queue
# =====================================================================================
#
# test_check_ins.py's FakePool serves primed values strictly in order. Extraction issues a
# VARIABLE number of db calls (one resolve per exercise, N set inserts, ...), so a fixed
# queue would encode a call count the table never specified and break on any harmless
# reordering. This fake instead answers by QUERY SHAPE and records every (query, args) in
# order, with transaction enter/exit markers, so the assertions stay about values.
#
# Identity SQL (`set_config` / `set local role`, issued by authed_conn) is recorded
# separately, so `events` holds only real queries — exactly like test_check_ins.py.

_TXN_ENTER = "__txn_enter__"
_TXN_EXIT = "__txn_exit__"


def _check_in_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": CHECK_IN_ID,
        "raw_text": "bench 135 4x8",
        "source": "text",
        "entry_date": date(2026, 7, 15),
        "created_at": CREATED_AT,
        "extraction_status": "pending",
    }
    row.update(overrides)
    return row


def _is_identity(query: str) -> bool:
    q = query.strip().lower()
    return "set_config" in q or q.startswith("set local role")


class _FakeTxn:
    def __init__(self, conn: "_FakeConn") -> None:
        self._conn = conn

    async def __aenter__(self) -> "_FakeTxn":
        self._conn.events.append((_TXN_ENTER, ()))
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        self._conn.events.append((_TXN_EXIT, ()))
        return False


class _FakeConn:
    def __init__(self, *, timezone: str | None, weight_unit: str, row: dict[str, Any]) -> None:
        self._timezone = timezone
        self._weight_unit = weight_unit
        self._row = row
        self.events: list[tuple[str, tuple[Any, ...]]] = []
        self.identity_calls: list[tuple[str, tuple[Any, ...]]] = []

    def transaction(self) -> _FakeTxn:
        return _FakeTxn(self)

    # --- routing -------------------------------------------------------------------

    def _record(self, query: str, args: tuple[Any, ...]) -> bool:
        if _is_identity(query):
            self.identity_calls.append((query, args))
            return False
        self.events.append((query, args))
        return True

    def _check_in_reply(self, args: tuple[Any, ...]) -> dict[str, Any]:
        """Echo a check_ins row, honouring any extraction_status bound in the statement.

        Serves both plausible implementations: insert-as-'pending' then update to the
        decided status, or insert once with it. Either way the row the app sees back
        carries the status it actually wrote.
        """
        row = dict(self._row)
        status = next((a for a in args if isinstance(a, str) and a in _STATUSES), None)
        if status is not None:
            row["extraction_status"] = status
        # The INSERT binds raw_text; the status UPDATE doesn't, so fall back to the primed
        # row. `source` ('text') is server-stamped in the SQL literal, never an arg.
        raw_text = next((a for a in args if isinstance(a, str) and a not in _STATUSES), None)
        if raw_text is not None:
            row["raw_text"] = raw_text
        return row

    def _route(self, query: str, args: tuple[Any, ...], *, scalar: bool) -> Any:
        q = " ".join(query.split()).lower()

        if "resolve_exercise" in q:
            raw = next((a for a in args if isinstance(a, str)), "")
            return _fake_resolve_exercise(raw)

        if "public.profiles" in q:
            if scalar:
                return self._weight_unit if "weight_unit" in q else self._timezone
            return {
                "id": USER_ID,
                "display_name": None,
                "weight_unit": self._weight_unit,
                "goal": None,
                "timezone": self._timezone,
                "consented_at": None,
                "created_at": CREATED_AT,
            }

        if "public.check_ins" in q and ("insert" in q or "update" in q):
            row = self._check_in_reply(args)
            return row["id"] if scalar else row

        if "public.check_ins" in q and "select" in q:
            return [self._row] if not scalar else self._row["id"]

        if "insert into" in q and "returning" in q:
            return uuid.uuid4() if scalar else {"id": uuid.uuid4()}

        return None

    # --- asyncpg surface -----------------------------------------------------------

    async def execute(self, query: str, *args: Any) -> str:
        self._record(query, args)
        return "OK"

    async def executemany(self, query: str, args_seq: Any) -> None:
        for args in args_seq:
            self._record(query, tuple(args))

    async def fetchval(self, query: str, *args: Any) -> Any:
        if not self._record(query, args):
            return None
        return self._route(query, args, scalar=True)

    async def fetchrow(self, query: str, *args: Any) -> Any:
        if not self._record(query, args):
            return None
        return self._route(query, args, scalar=False)

    async def fetch(self, query: str, *args: Any) -> Any:
        if not self._record(query, args):
            return []
        result = self._route(query, args, scalar=False)
        if result is None:
            return []
        return result if isinstance(result, list) else [result]


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class FakePool:
    def __init__(self, *, timezone: str | None, weight_unit: str, row: dict[str, Any]) -> None:
        self.conn = _FakeConn(timezone=timezone, weight_unit=weight_unit, row=row)

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self.conn)


# =====================================================================================
# The fake Extractor (injected via ExtractorDep — CI never calls the live API)
# =====================================================================================


class FakeExtractor:
    """Returns primed facts, or raises — standing in for `HaikuExtractor`."""

    def __init__(self, facts: Any = None, error: Exception | None = None) -> None:
        self._facts = facts
        self._error = error
        self.texts: list[str] = []

    async def extract(self, text: str) -> Any:
        self.texts.append(text)
        if self._error is not None:
            raise self._error
        return self._facts


def _facts(**kwargs: Any) -> Any:
    """Build an `ExtractedFacts`. Lazy import: at oracle time this raises ImportError."""
    from app.schemas.extraction import ExtractedFacts

    return ExtractedFacts(**kwargs)


def _set(exercise_name: str, set_number: int, reps: int, weight: Decimal | None) -> Any:
    from app.schemas.extraction import ExtractedSet

    return ExtractedSet(
        exercise_name=exercise_name, set_number=set_number, reps=reps, weight=weight
    )


def _sign_in(
    extractor: FakeExtractor,
    *,
    weight_unit: str = "lb",
    timezone: str | None = "UTC",
    row: dict[str, Any] | None = None,
) -> FakePool:
    """Wire the app: USER_ID holds a valid token, the pool is fake, the Extractor is fake."""
    from app.ai.extractor import get_extractor

    from app.deps import get_pool

    pool = FakePool(timezone=timezone, weight_unit=weight_unit, row=row or _check_in_row())
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[get_extractor] = lambda: extractor
    return pool


# --- assertion helpers ---------------------------------------------------------------


def _sql(pool: FakePool) -> list[tuple[str, tuple[Any, ...]]]:
    return [(q, a) for q, a in pool.conn.events if q not in (_TXN_ENTER, _TXN_EXIT)]


def _inserts_into(pool: FakePool, table: str) -> list[tuple[Any, ...]]:
    """The argument tuples of every INSERT into `public.<table>`, in order."""
    out: list[tuple[Any, ...]] = []
    for query, args in _sql(pool):
        q = " ".join(query.split()).lower()
        if q.startswith("insert into public." + table) or f"insert into public.{table} " in q:
            out.append(args)
    return out


def _touches(pool: FakePool, needle: str) -> list[tuple[str, tuple[Any, ...]]]:
    return [(q, a) for q, a in _sql(pool) if needle in " ".join(q.split()).lower()]


# =====================================================================================
# Section A — mapping & persistence
# =====================================================================================
#
# The fact-insert argument ORDER these tests pin is the migration's own column order:
#   workout_sets       (user_id, check_in_id, exercise_id, set_number, reps, weight_kg)
#   nutrition_entries  (user_id, check_in_id, description, calories, protein_g, carbs_g,
#                       fat_g, meal)
#   sleep_entries      (user_id, check_in_id, hours, quality)
#   bodyweight_entries (user_id, check_in_id, weight_kg)
# Pinning it is what lets these assert VALUES ("weight_kg is 61.235", "weight_kg is null,
# not 0") instead of the un-assertion "some 61.235 appears somewhere in the args".


# AC row 1: extractor returns 4 sets of 8 @ 135, profile weight_unit = 'lb'
#   -> 4 workout_sets rows; set_number 1,2,3,4; reps 8; weight_kg 61.235 each.
async def test_row1_four_sets_at_135_lb_convert_to_61_235_kg(client: AsyncClient) -> None:
    extractor = FakeExtractor(
        _facts(sets=[_set("bench press", n, 8, Decimal("135")) for n in (1, 2, 3, 4)])
    )
    pool = _sign_in(extractor, weight_unit="lb")

    resp = await client.post("/check-ins", json={"text": "bench 135 4x8"})

    assert resp.status_code == 201
    ex = exercise_id("bench press")
    assert _inserts_into(pool, "workout_sets") == [
        (USER_ID, CHECK_IN_ID, ex, 1, 8, BENCH_135_LB_IN_KG),
        (USER_ID, CHECK_IN_ID, ex, 2, 8, BENCH_135_LB_IN_KG),
        (USER_ID, CHECK_IN_ID, ex, 3, 8, BENCH_135_LB_IN_KG),
        (USER_ID, CHECK_IN_ID, ex, 4, 8, BENCH_135_LB_IN_KG),
    ]


# AC row 2: same input, profile weight_unit = 'kg' -> 4 rows, weight_kg = 135.
# The unit comes from the PROFILE, never from the text: same extractor output, same
# request body, different stored kg. Nothing but the profile read can explain the delta.
async def test_row2_same_sets_with_kg_profile_store_135_kg(client: AsyncClient) -> None:
    extractor = FakeExtractor(
        _facts(sets=[_set("bench press", n, 8, Decimal("135")) for n in (1, 2, 3, 4)])
    )
    pool = _sign_in(extractor, weight_unit="kg")

    resp = await client.post("/check-ins", json={"text": "bench 135 4x8"})

    assert resp.status_code == 201
    ex = exercise_id("bench press")
    assert _inserts_into(pool, "workout_sets") == [
        (USER_ID, CHECK_IN_ID, ex, 1, 8, Decimal("135")),
        (USER_ID, CHECK_IN_ID, ex, 2, 8, Decimal("135")),
        (USER_ID, CHECK_IN_ID, ex, 3, 8, Decimal("135")),
        (USER_ID, CHECK_IN_ID, ex, 4, 8, Decimal("135")),
    ]


# AC row 3: a bodyweight move (20 pushups, no load) -> 1 row, weight_kg is NULL.
# Explicitly `is None` and `!= 0`: writing 0 would drag a strength average toward zero,
# and 0 == False-ish checks are exactly how that bug gets written.
async def test_row3_bodyweight_move_stores_null_weight_not_zero(client: AsyncClient) -> None:
    extractor = FakeExtractor(_facts(sets=[_set("pushups", 1, 20, None)]))
    pool = _sign_in(extractor, weight_unit="lb")

    resp = await client.post("/check-ins", json={"text": "20 pushups"})

    assert resp.status_code == 201
    rows = _inserts_into(pool, "workout_sets")
    assert rows == [(USER_ID, CHECK_IN_ID, exercise_id("pushups"), 1, 20, None)]
    assert rows[0][5] is None  # null for bodyweight moves...
    assert rows[0][5] != 0  # ...never 0


# AC row 4: sleep 6h with no quality -> 1 sleep_entries row, hours 6, quality NULL.
# Inventing a 3/5 is fabrication, so quality must be exactly None.
async def test_row4_sleep_without_quality_stores_null_quality(client: AsyncClient) -> None:
    from app.schemas.extraction import ExtractedSleep

    extractor = FakeExtractor(_facts(sleep=[ExtractedSleep(hours=Decimal("6"), quality=None)]))
    pool = _sign_in(extractor)

    resp = await client.post("/check-ins", json={"text": "slept 6h"})

    assert resp.status_code == 201
    rows = _inserts_into(pool, "sleep_entries")
    assert rows == [(USER_ID, CHECK_IN_ID, Decimal("6"), None)]
    assert rows[0][3] is None  # quality is nullable and stays null


# AC row 5: "4 eggs" + macros -> 1 nutrition_entries row, all four macros non-null,
# meal NULL. All four macros are NOT NULL in the schema, so the boundary can never
# say "I don't know" — it must carry real numbers through.
async def test_row5_nutrition_stores_all_four_macros_and_null_meal(client: AsyncClient) -> None:
    from app.schemas.extraction import ExtractedNutrition

    extractor = FakeExtractor(
        _facts(
            nutrition=[
                ExtractedNutrition(
                    description="4 eggs",
                    calories=Decimal("310"),
                    protein_g=Decimal("25"),
                    carbs_g=Decimal("2"),
                    fat_g=Decimal("22"),
                    meal=None,
                )
            ]
        )
    )
    pool = _sign_in(extractor)

    resp = await client.post("/check-ins", json={"text": "ate 4 eggs"})

    assert resp.status_code == 201
    rows = _inserts_into(pool, "nutrition_entries")
    assert rows == [
        (
            USER_ID,
            CHECK_IN_ID,
            "4 eggs",
            Decimal("310"),
            Decimal("25"),
            Decimal("2"),
            Decimal("22"),
            None,
        )
    ]
    _uid, _cid, _desc, calories, protein, carbs, fat, meal = rows[0]
    assert all(v is not None for v in (calories, protein, carbs, fat))  # NOT NULL columns
    assert meal is None  # nullable, and not invented


# AC row 6: bodyweight 180 with weight_unit 'lb' -> 1 bodyweight_entries row, 81.647 kg.
async def test_row6_bodyweight_180_lb_converts_to_81_647_kg(client: AsyncClient) -> None:
    from app.schemas.extraction import ExtractedBodyweight

    extractor = FakeExtractor(_facts(bodyweight=[ExtractedBodyweight(weight=Decimal("180"))]))
    pool = _sign_in(extractor, weight_unit="lb")

    resp = await client.post("/check-ins", json={"text": "weighed in at 180"})

    assert resp.status_code == 201
    rows = _inserts_into(pool, "bodyweight_entries")
    assert rows == [(USER_ID, CHECK_IN_ID, BODYWEIGHT_180_LB_IN_KG)]
    assert rows[0][2] > 0  # bodyweight_entries.weight_kg > 0 (strict), unlike workout_sets


# AC row 7: "Bench Press" then a later check-in's "bench press" -> ONE exercises row, both
# sets point at it. At this tier that means: BOTH names go through the guarded
# `public.resolve_exercise` door (the only sanctioned write path), the app NEVER writes
# `exercises` itself, and both sets take the id the guard returned. That the guard folds
# case to a single row is SQL behaviour, proven against the real DB in test_extraction_db.
async def test_row7_both_casings_resolve_through_the_guard_to_one_id(client: AsyncClient) -> None:
    first = FakeExtractor(_facts(sets=[_set("Bench Press", 1, 8, Decimal("135"))]))
    pool_a = _sign_in(first, weight_unit="kg")
    assert (
        await client.post("/check-ins", json={"text": "Bench Press 135 1x8"})
    ).status_code == 201

    second = FakeExtractor(_facts(sets=[_set("bench press", 1, 8, Decimal("135"))]))
    pool_b = _sign_in(second, weight_unit="kg", row=_check_in_row(id=SECOND_CHECK_IN_ID))
    assert (
        await client.post("/check-ins", json={"text": "bench press 135 1x8"})
    ).status_code == 201

    # Both went through the guarded door, with the RAW name (the guard normalizes).
    assert [a for _q, a in _touches(pool_a, "resolve_exercise")] == [("Bench Press",)]
    assert [a for _q, a in _touches(pool_b, "resolve_exercise")] == [("bench press",)]

    # ...and both sets point at the SAME exercise id.
    ex = exercise_id("bench press")
    assert _inserts_into(pool_a, "workout_sets") == [
        (USER_ID, CHECK_IN_ID, ex, 1, 8, Decimal("135"))
    ]
    assert _inserts_into(pool_b, "workout_sets") == [
        (USER_ID, SECOND_CHECK_IN_ID, ex, 1, 8, Decimal("135"))
    ]

    # The guarded function is the ONLY write path: no hand-rolled insert into exercises.
    assert _inserts_into(pool_a, "exercises") == []
    assert _inserts_into(pool_b, "exercises") == []


# AC row 8: a re-run on a check-in that already has facts REPLACES the derived rows rather
# than duplicating them, in ONE transaction. No unique constraint exists, so a re-run that
# only inserts silently doubles every set.
#
# Driven at the service layer: nothing in #19's scope re-runs extraction over HTTP (POST
# /check-ins always creates a fresh check-in), so `extract_and_store` is the only surface
# where "again, on the same check_in_id" is even expressible.
#
# What a fake CAN prove: each run DELETEs the check-in's existing derived rows (scoped to
# id AND user_id) BEFORE inserting, and the whole replace lives inside a single
# transaction. What it CANNOT prove is the row count afterwards — that needs a real DB and
# is covered end-to-end by row 8's real-DB counterpart in test_extraction_db.py.
async def test_row8_rerun_replaces_facts_in_one_transaction() -> None:
    from app.services.extraction import extract_and_store

    extractor = FakeExtractor(_facts(sets=[_set("bench press", 1, 8, Decimal("135"))]))
    pool = FakePool(timezone="UTC", weight_unit="kg", row=_check_in_row())

    await extract_and_store(pool, USER_ID, CHECK_IN_ID, "bench 135 1x8", extractor)

    events = pool.conn.events
    # Group the recorded SQL into transaction spans; keep the spans that touch facts.
    spans: list[list[tuple[str, tuple[Any, ...]]]] = []
    current: list[tuple[str, tuple[Any, ...]]] | None = None
    for query, args in events:
        if query == _TXN_ENTER:
            current = []
        elif query == _TXN_EXIT:
            if current:
                spans.append(current)
            current = None
        elif current is not None:
            current.append((query, args))

    fact_spans = [
        span
        for span in spans
        if any("workout_sets" in " ".join(q.split()).lower() for q, _a in span)
    ]
    assert len(fact_spans) == 1, "the replace must be ONE transaction, not delete-then-insert races"

    span = fact_spans[0]
    kinds = [" ".join(q.split()).lower() for q, _a in span]
    delete_idx = next(
        i for i, q in enumerate(kinds) if q.startswith("delete from public.workout_sets")
    )
    insert_idx = next(
        i for i, q in enumerate(kinds) if q.startswith("insert into public.workout_sets")
    )
    assert delete_idx < insert_idx  # replace, not append

    # The delete is owner-scoped in the SAME statement (backend rule 2).
    delete_query, delete_args = span[delete_idx]
    assert "user_id" in " ".join(delete_query.split()).lower()
    assert CHECK_IN_ID in delete_args
    assert USER_ID in delete_args


# =====================================================================================
# Section B — failure & resilience (the issue's hard rule: never lose the raw text)
# =====================================================================================


# AC row 9: the Haiku call times out / 500s -> 201, check-in saved with raw_text intact,
# extraction_status 'failed', ZERO fact rows. A dead vendor must not eat your words or
# fail your request.
@pytest.mark.parametrize(
    "error",
    [TimeoutError("haiku timed out"), RuntimeError("haiku returned 500")],
    ids=["timeout", "500"],
)
async def test_row9_extractor_failure_still_saves_raw_text_and_marks_failed(
    client: AsyncClient, error: Exception
) -> None:
    text = "bench 135 4x8"
    extractor = FakeExtractor(error=error)
    pool = _sign_in(extractor, row=_check_in_row(raw_text=text))

    resp = await client.post("/check-ins", json={"text": text})

    assert resp.status_code == 201
    body = resp.json()
    assert body["raw_text"] == text  # verbatim; the vendor did not eat it
    assert body["extraction_status"] == "failed"
    for table in ("workout_sets", "nutrition_entries", "sleep_entries", "bodyweight_entries"):
        assert _inserts_into(pool, table) == []


# AC row 10 (schema half): Haiku's junk is REJECTED BEFORE THE DB by the Pydantic models
# that mirror the CHECK constraints — reps = -1 (`reps >= 0`) and hours = 30
# (`hours between 0 and 24`). AI output is untrusted input.
def test_row10_extraction_schema_rejects_negative_reps() -> None:
    from app.schemas.extraction import ExtractedSet
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ExtractedSet(exercise_name="bench press", set_number=1, reps=-1, weight=Decimal("135"))


def test_row10_extraction_schema_rejects_30_hours_of_sleep() -> None:
    from app.schemas.extraction import ExtractedSleep
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ExtractedSleep(hours=Decimal("30"), quality=None)


# AC row 10 (schema half, the other CHECK mirrors): quality is 1-5, macros are >= 0,
# bodyweight kg is strictly > 0. Each of these is a DB CHECK the boundary must enforce
# first, so junk never reaches Postgres.
def test_row10_extraction_schema_mirrors_the_remaining_db_checks() -> None:
    from app.schemas.extraction import ExtractedBodyweight, ExtractedNutrition, ExtractedSleep
    from pydantic import ValidationError

    with pytest.raises(ValidationError):  # quality between 1 and 5
        ExtractedSleep(hours=Decimal("6"), quality=6)
    with pytest.raises(ValidationError):
        ExtractedSleep(hours=Decimal("6"), quality=0)
    with pytest.raises(ValidationError):  # macros >= 0
        ExtractedNutrition(
            description="4 eggs",
            calories=Decimal("-1"),
            protein_g=Decimal("25"),
            carbs_g=Decimal("2"),
            fat_g=Decimal("22"),
            meal=None,
        )
    with pytest.raises(ValidationError):  # bodyweight_entries.weight_kg > 0 (strict)
        ExtractedBodyweight(weight=Decimal("0"))


# AC row 10 (endpoint half): when Haiku's junk fails validation, the request is still 201,
# the raw text is intact, status is 'failed', and nothing reaches the fact tables.
async def test_row10_junk_from_haiku_fails_extraction_but_keeps_raw_text(
    client: AsyncClient,
) -> None:
    from app.schemas.extraction import ExtractedSet
    from pydantic import ValidationError

    text = "bench 135 4x8, slept 30h"
    try:
        ExtractedSet(exercise_name="bench press", set_number=1, reps=-1, weight=None)
    except ValidationError as exc:
        junk_error: Exception = exc
    else:  # pragma: no cover - the schema half above already pins this
        pytest.fail("ExtractedSet accepted reps=-1; row 10's boundary is missing")

    extractor = FakeExtractor(error=junk_error)
    pool = _sign_in(extractor, row=_check_in_row(raw_text=text))

    resp = await client.post("/check-ins", json={"text": text})

    assert resp.status_code == 201
    assert resp.json()["raw_text"] == text
    assert resp.json()["extraction_status"] == "failed"
    for table in ("workout_sets", "nutrition_entries", "sleep_entries", "bodyweight_entries"):
        assert _inserts_into(pool, table) == []


# AC row 11: non-fitness text ("what's the weather") -> zero fact rows, check-in saved,
# extraction_status 'done' (Toby's decision: "nothing to extract" is SUCCESS, not failure).
async def test_row11_non_fitness_text_is_done_with_zero_facts(client: AsyncClient) -> None:
    text = "what's the weather"
    extractor = FakeExtractor(_facts())  # the model found nothing to extract
    pool = _sign_in(extractor, row=_check_in_row(raw_text=text))

    resp = await client.post("/check-ins", json={"text": text})

    assert resp.status_code == 201
    body = resp.json()
    assert body["raw_text"] == text
    assert body["extraction_status"] == "done"  # NOT 'failed'
    for table in ("workout_sets", "nutrition_entries", "sleep_entries", "bodyweight_entries"):
        assert _inserts_into(pool, table) == []


# AC row 12: partial text "bench 135 4x8 and my boss is annoying" -> the sets are written,
# ZERO junk rows land in the other fact tables, and the raw text is whole.
async def test_row12_partial_text_writes_sets_only_and_keeps_raw_text(client: AsyncClient) -> None:
    text = "bench 135 4x8 and my boss is annoying"
    extractor = FakeExtractor(
        _facts(sets=[_set("bench press", n, 8, Decimal("135")) for n in (1, 2, 3, 4)])
    )
    pool = _sign_in(extractor, weight_unit="lb", row=_check_in_row(raw_text=text))

    resp = await client.post("/check-ins", json={"text": text})

    assert resp.status_code == 201
    assert resp.json()["raw_text"] == text  # whole, verbatim — the note is not truncated
    ex = exercise_id("bench press")
    assert _inserts_into(pool, "workout_sets") == [
        (USER_ID, CHECK_IN_ID, ex, n, 8, BENCH_135_LB_IN_KG) for n in (1, 2, 3, 4)
    ]
    for table in ("nutrition_entries", "sleep_entries", "bodyweight_entries"):
        assert _inserts_into(pool, table) == []  # "my boss is annoying" is not a fact row


# AC row 13: exercise name "bench press — hmu at toby@gmail.com" -> the guard REJECTS the
# name, THAT set is dropped, the other facts still save, status = 'partial'.
# `exercises` is ownerless and shared: no user data may ever land in it.
async def test_row13_rejected_exercise_name_drops_that_set_and_marks_partial(
    client: AsyncClient,
) -> None:
    from app.schemas.extraction import ExtractedSleep

    leaky = "bench press — hmu at toby@gmail.com"
    extractor = FakeExtractor(
        _facts(
            sets=[_set(leaky, 1, 8, Decimal("135"))],
            sleep=[ExtractedSleep(hours=Decimal("6"), quality=None)],
        )
    )
    pool = _sign_in(extractor, weight_unit="lb")

    resp = await client.post("/check-ins", json={"text": leaky + ", slept 6h"})

    assert resp.status_code == 201
    assert resp.json()["extraction_status"] == "partial"
    assert _inserts_into(pool, "workout_sets") == []  # that set is dropped
    assert _inserts_into(pool, "sleep_entries") == [  # the other facts still save
        (USER_ID, CHECK_IN_ID, Decimal("6"), None)
    ]
    # The leak this design exists to prevent: the address never reaches the shared catalog.
    assert _inserts_into(pool, "exercises") == []
    for query, args in _sql(pool):
        if "public.exercises" in " ".join(query.split()).lower():
            assert "toby@gmail.com" not in str(args)
    # The name was rejected BY THE GUARD, not by a hand-rolled check that skipped the door.
    assert [a for _q, a in _touches(pool, "resolve_exercise")] == [(leaky,)]
