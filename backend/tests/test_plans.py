"""Oracle suite for issue #51 — the plan. Tier U (fake planner, fake pool, no database).

This file is part of commit #1 on `feat/plan-and-diet`, written BEFORE any implementation
exists. It encodes the 26-row correctness table Toby approved in the body of issue #51 and
re-affirmed as the approval of record on 2026-08-23 — and nothing else. At oracle time
`from app.schemas.plans import PlanTemplate`, `from app.ai.planner import get_planner`,
`from app.services.plans import materialize` and `POST /plans` all fail: ImportError and
404. That is the CORRECT failure.

  issue:      https://github.com/tobyliu2004/coach-bill/issues/51
  approval:   https://github.com/tobyliu2004/coach-bill/issues/51#issuecomment-5388596176
  amendments: https://github.com/tobyliu2004/coach-bill/issues/51#issuecomment-5388619729
              https://github.com/tobyliu2004/coach-bill/issues/51#issuecomment-5389340006

Which rows live where:

1. THIS FILE — rows 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 26. Everything a fake pool and a
   fake `Planner` can honestly grade: status codes, what got written (and what did NOT),
   the pure expansion, client-input bounds, the local-date doctrine, and the SQL TEXT of
   `db/plans.py`.
   ⚠️ Row 20 is NOT in that list, and it was in the first draft of this file. Amendment 3
   removed it — a WIDENING edit that can newly pass. Section D below is the audit trail and
   names where the property is proven instead; read it before assuming a row is missing.
2. tests/test_plans_db.py — rows 11, 13, 14, 15, 16, 17. A fake cannot execute
   `where ... and user_id = $1`, cannot enforce a partial unique index, cannot lose a race
   and cannot refuse an UPDATE for want of a grant. Those rows are only real there.
3. tests/test_plans_live_model.py — rows L1-L4 of amendment 2. The only real check on the
   new planner prompt; never runs in CI.
4. Rows 18, 19, 20 (see section D), 21, 22, 23, 24, 25 are FE (vitest) and live in
   another agent's file.

Row 16's table-level grant matrix is amendment 1 and lives in tests/test_table_privileges.py
(#37's frozen oracle), because a second parallel matrix would drift from the audited one.

⚠️ THE FAKE'S SQL ROUTER IS MACHINERY, NOT AN ASSERTION.
`_FakeDb` below parses INSERT statements and answers reads out of what it stored, so the
assertions can be about ROWS ("28 plan_days landed, each with a focus") rather than about a
statement shape nobody approved. If the implementation issues a statement the router cannot
parse, the router raises `_FakeSqlError` naming the statement: that is a MACHINERY GAP —
teach the router the shape, and never touch an assertion to route around it. The assertions
are the oracle; the router is a stand-in for Postgres.

Every test names the AC row it covers.
"""

import ast
import inspect
import re
import uuid
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.auth import get_current_user_id
from app.main import app

USER_ID = uuid.uuid4()
CREATED_AT = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)

# The three tables this feature owns. Rows 2 and 5 are assertions about ALL of them.
_PLAN_TABLES = ("plans", "plan_days", "plan_items")

# The user-owned tables row 12 fences. `exercises` is the ONE ownerless table
# (a shared catalog, `.claude/rules/backend.md`) and is deliberately absent: it has no
# `user_id` column and must never be asserted to have one.
_OWNED_TABLES = frozenset(
    {
        "plans",
        "plan_days",
        "plan_items",
        "check_ins",
        "workout_sets",
        "nutrition_entries",
        "sleep_entries",
        "bodyweight_entries",
        "coach_messages",
        "profiles",
    }
)

# A seeded catalog, keyed by the normalized name `resolve_exercise` looks up. Rows 3 and 4
# turn on a name that is NOT in here.
CATALOG: dict[str, uuid.UUID] = {
    "bench press": uuid.uuid4(),
    "back squat": uuid.uuid4(),
    "deadlift": uuid.uuid4(),
    "overhead press": uuid.uuid4(),
    "barbell row": uuid.uuid4(),
}
UNRESOLVABLE = "the john smith special"  # a name no seeded catalog contains


# =====================================================================================
# SQL text helpers — shared by the fake router and by row 12's source scan
# =====================================================================================


def _normalize(query: str) -> str:
    """Lowercase, collapse whitespace, standardise spacing around `=`.

    Statements are built by concatenating adjacent string literals, so a clause can be split
    across Python source lines; normalising is what makes "contiguous" mean contiguous in the
    SQL rather than contiguous in the file. Spacing around `=` is style, not security, so
    `pd.user_id=$1` and `pd.user_id = $1` are the same clause — nothing else is.

    A FILE-LOCAL copy of tests/test_trends.py's helper, per PR #45's precedent, so this
    suite's security guard cannot be weakened from a distance.
    """
    collapsed = " ".join(query.lower().split())
    return re.sub(r"\s*=\s*", " = ", collapsed)


# Words that follow a table name when it has no alias at all (`from public.plans where`).
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
        "returning",
        "right",
        "set",
        "union",
        "using",
        "values",
        "where",
        "window",
    }
)


def _alias_for(query: str, table: str) -> str | None:
    """The alias `public.<table>` is bound to in this statement, or the bare table name."""
    match = re.search(rf"public\.{table}\b\s*(?:as\s+)?(\w+)?", query)
    if match is None:
        return None
    alias = match.group(1)
    if alias is None or alias in _ALIAS_STOPWORDS:
        return table
    return alias


# =====================================================================================
# The fake database
# =====================================================================================


class _FakeSqlError(AssertionError):
    """The router met a statement shape it cannot answer. A MACHINERY gap, not a failure
    of the code under test — see the warning in this module's docstring."""


_INSERT_HEAD = re.compile(r"insert into public\.(\w+)\s*\(([^)]*)\)\s*(.*)$")
_PLACEHOLDER = re.compile(r"\$(\d+)")


def _split_top_level(text: str) -> list[str]:
    """Split on commas that are not inside parentheses."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    parts.append("".join(current).strip())
    return [p for p in parts if p]


def _parse_insert(query: str, args: tuple[Any, ...]) -> tuple[str, list[dict[str, Any]]]:
    """(table, rows) for an INSERT, whatever bulk shape it was written in.

    Understands the three shapes this codebase plausibly uses, so the oracle does not fix a
    statement shape the approved table never mentions:
      * `values ($1, $2, ...)` — one or many tuples, and `executemany` over one tuple.
      * `select $1, $2, ... where exists (...)` — db/facts.py's guarded-write house style.
      * `select $1, unnest($2::date[]), ...` and
        `select $1, u.day_date, ... from unnest($2::date[], $3::text[]) as u(day_date, ...)`
        — the two bulk-array shapes.
    Anything else raises `_FakeSqlError` naming the statement, loudly.
    """
    q = _normalize(query)
    head = _INSERT_HEAD.match(q)
    if head is None:
        raise _FakeSqlError(f"the fake's INSERT router cannot parse: {q}")
    table, column_text, body = head.group(1), head.group(2), head.group(3)
    columns = [c.strip() for c in column_text.split(",") if c.strip()]

    for tail in (" returning ", " on conflict ", " where exists"):
        cut = body.find(tail)
        if cut != -1:
            body = body[:cut]
    body = body.strip()

    alias: str | None = None
    arrays: dict[str, Any] = {}
    if body.startswith("values"):
        groups = re.findall(r"\(([^()]*)\)", body[len("values") :])
        expressions = [_split_top_level(group) for group in groups]
    elif body.startswith("select"):
        select_text = body[len("select") :]
        from_match = re.search(r"\bfrom\b(.*)$", select_text)
        if from_match is not None:
            select_text = select_text[: from_match.start()]
            from_clause = from_match.group(1)
            unnest = re.search(r"unnest\((.*?)\)\s*(?:as\s+)?(\w+)\s*\(([^)]*)\)", from_clause)
            if unnest is not None:
                placeholders = _PLACEHOLDER.findall(unnest.group(1))
                alias = unnest.group(2)
                names = [n.strip() for n in unnest.group(3).split(",") if n.strip()]
                if len(names) != len(placeholders):
                    raise _FakeSqlError(f"the fake cannot map this unnest alias: {q}")
                arrays = {n: args[int(p) - 1] for n, p in zip(names, placeholders, strict=True)}
        expressions = [_split_top_level(select_text)]
    else:
        raise _FakeSqlError(f"the fake's INSERT router cannot parse the source of: {q}")

    rows: list[dict[str, Any]] = []
    for group in expressions:
        if len(group) != len(columns):
            raise _FakeSqlError(
                f"the fake counted {len(group)} values for {len(columns)} columns in: {q}"
            )
        width = _row_count(group, args, alias, arrays)
        for index in range(width):
            rows.append(
                {
                    column: _eval_insert_expression(expr, index, args, alias, arrays)
                    for column, expr in zip(columns, group, strict=True)
                }
            )
    return table, rows


def _row_count(
    group: list[str], args: tuple[Any, ...], alias: str | None, arrays: dict[str, Any]
) -> int:
    """How many rows one value-group produces — >1 only for the array (unnest) shapes."""
    lengths = [len(value) for value in arrays.values() if isinstance(value, list)]
    for expr in group:
        unnest = re.fullmatch(r"unnest\(\s*\$(\d+)(::[\w\[\] ]+)?\s*\)", expr.strip())
        if unnest is not None:
            value = args[int(unnest.group(1)) - 1]
            if isinstance(value, list):
                lengths.append(len(value))
    return max(lengths) if lengths else 1


def _eval_insert_expression(
    expr: str, index: int, args: tuple[Any, ...], alias: str | None, arrays: dict[str, Any]
) -> Any:
    """One column's value for row `index` of an INSERT."""
    text = expr.strip()
    bare = text.split("::")[0].strip()
    placeholder = _PLACEHOLDER.fullmatch(bare)
    if placeholder is not None:
        return args[int(placeholder.group(1)) - 1]
    unnest = re.fullmatch(r"unnest\(\s*\$(\d+)(::[\w\[\] ]+)?\s*\)", text)
    if unnest is not None:
        value = args[int(unnest.group(1)) - 1]
        return value[index] if isinstance(value, list) else value
    if alias is not None and text.startswith(f"{alias}."):
        column = text.split(".", 1)[1].split("::")[0].strip()
        value = arrays.get(column)
        return value[index] if isinstance(value, list) else value
    if text.startswith("'") and text.endswith("'"):
        return text[1:-1]
    if text in ("default", "now()", "gen_random_uuid()", "current_date", "null"):
        return None
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return Decimal(text)
    raise _FakeSqlError(f"the fake cannot evaluate the INSERT expression {text!r}")


class _FakeDb:
    """The three plan tables, a profile, and a seeded exercise catalog.

    Stateful on purpose: rows 1/3/4/9/10 are questions about WHAT WAS WRITTEN, and a
    positionally-primed fake would answer them with its own priming instead.
    """

    def __init__(
        self,
        *,
        tz: str | None = "UTC",
        goal: str | None = "get stronger",
        weight_unit: str = "lb",
    ) -> None:
        self.tz = tz
        self.goal = goal
        self.weight_unit = weight_unit
        self.tables: dict[str, list[dict[str, Any]]] = {t: [] for t in _PLAN_TABLES}

    # ---------------------------------------------------------------- writes

    def _insert(self, query: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        table, rows = _parse_insert(query, args)
        if table not in self.tables:
            raise _FakeSqlError(f"this feature must not insert into public.{table}: {query}")
        stored: list[dict[str, Any]] = []
        for row in rows:
            row.setdefault("id", uuid.uuid4())
            if row.get("id") is None:
                row["id"] = uuid.uuid4()
            row.setdefault("created_at", CREATED_AT)
            if table == "plans":
                row.setdefault("status", "active")
            if table == "plan_days":
                row.setdefault("logged", False)
            if table == "plan_items":
                row.setdefault("exercise_name", self._name_of(row.get("exercise_id")))
            self.tables[table].append(row)
            stored.append(row)
        return stored

    def _name_of(self, exercise_id: Any) -> str | None:
        for name, ident in CATALOG.items():
            if ident == exercise_id:
                return name
        return None

    def _update_plans(self, query: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        q = _normalize(query)
        match = re.search(r"set status = '(\w+)'", q)
        if match is None:
            raise _FakeSqlError(f"the fake only understands `set status = '<value>'`: {q}")
        touched: list[dict[str, Any]] = []
        for row in self.tables["plans"]:
            if not _matches_args(row, args):
                continue
            if "status = 'active'" in q.split(" where ", 1)[-1] and row["status"] != "active":
                continue
            row["status"] = match.group(1)
            touched.append(row)
        return touched

    # ---------------------------------------------------------------- reads

    def route(self, method: str, query: str, args: tuple[Any, ...]) -> Any:
        q = _normalize(query)

        if q.startswith("insert into"):
            return self._insert(query, args)
        if q.startswith("update public.plans"):
            return self._update_plans(query, args)
        if q.startswith("delete") and any(f"public.{t}" in q for t in _PLAN_TABLES):
            return self._delete(q, args)

        if "public.exercises" in q:
            name = next((a for a in args if isinstance(a, str)), "")
            found = CATALOG.get(name.strip().lower())
            return [{"id": found}] if found is not None else []

        if "public.profiles" in q:
            head = q.split(" from ")[0]
            if method == "fetchval":
                if "timezone" in head:
                    return [{"timezone": self.tz}]
                if "weight_unit" in head:
                    return [{"weight_unit": self.weight_unit}]
                if "goal" in head:
                    return [{"goal": self.goal}]
                return []
            return [
                {
                    "id": USER_ID,
                    "display_name": None,
                    "weight_unit": self.weight_unit,
                    "goal": self.goal,
                    "timezone": self.tz,
                    "consented_at": CREATED_AT,
                    "created_at": CREATED_AT,
                }
            ]

        touched = [t for t in ("plan_items", "plan_days", "plans") if f"public.{t}" in q]
        if touched:
            return self._read_plan_tables(q, args, touched)

        return []

    def _delete(self, q: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        for table in _PLAN_TABLES:
            if f"public.{table}" in q:
                keep = [r for r in self.tables[table] if not _matches_args(r, args)]
                self.tables[table] = keep
        return []

    def _read_plan_tables(
        self, q: str, args: tuple[Any, ...], touched: list[str]
    ) -> list[dict[str, Any]]:
        """Rows for a read of the plan tables, joined when the statement names two of them."""
        if "plan_items" in touched and "plan_days" in touched:
            rows = [
                {**self._day_of(item), **item}
                for item in self.tables["plan_items"]
                if self._day_of(item)
            ]
        elif "plan_items" in touched:
            rows = [
                {**item, "plan_id": self._day_of(item).get("plan_id")}
                for item in self.tables["plan_items"]
            ]
        elif "plan_days" in touched:
            rows = list(self.tables["plan_days"])
        else:
            rows = list(self.tables["plans"])

        if "status = 'active'" in q:
            rows = [r for r in rows if r.get("status", "active") == "active"]
        return [r for r in rows if _matches_args(r, args)]

    def _day_of(self, item: dict[str, Any]) -> dict[str, Any]:
        day_id = item.get("plan_day_id")
        for day in self.tables["plan_days"]:
            if day["id"] == day_id:
                return day
        return {}


def _matches_args(row: dict[str, Any], args: tuple[Any, ...]) -> bool:
    """Every uuid the statement bound must be one this row carries.

    Stands in for `where user_id = $1 and plan_id = $2` without the fake having to parse a
    WHERE clause: a statement that names A's id never gets B's row back. It is deliberately
    NOT a security assertion — row 12 is, and it reads the SQL text directly.
    """
    wanted = {a for a in args if isinstance(a, uuid.UUID)}
    if not wanted:
        return True
    held = {v for v in row.values() if isinstance(v, uuid.UUID)}
    return wanted <= held


def _is_identity_statement(query: str) -> bool:
    """`authed_conn`'s two per-transaction identity statements — not app queries."""
    q = _normalize(query)
    return "set_config" in q or q.startswith("set local role")


def _first_column(query: str) -> str:
    """The column a `fetchval` would come back with: `returning <col>`, else the select head."""
    q = _normalize(query)
    returning = re.search(r"returning\s+([\w.]+)", q)
    if returning is not None:
        return returning.group(1).split(".")[-1]
    head = re.match(r"select\s+(.*?)(?:\s+from\s+|$)", q)
    if head is not None:
        first = _split_top_level(head.group(1))[0]
        alias = re.search(r"\bas\s+(\w+)$", first)
        return alias.group(1) if alias else first.split(".")[-1]
    return "id"


class _FakeTxn:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FakeConn:
    def __init__(self, db: _FakeDb) -> None:
        self.db = db
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.identity_calls: list[tuple[str, tuple[Any, ...]]] = []

    def transaction(self) -> _FakeTxn:
        return _FakeTxn()

    def _record(self, query: str, args: tuple[Any, ...]) -> None:
        if _is_identity_statement(query):
            self.identity_calls.append((query, args))
        else:
            self.calls.append((query, args))

    async def execute(self, query: str, *args: Any) -> str:
        self._record(query, args)
        if not _is_identity_statement(query):
            self.db.route("execute", query, args)
        return "OK"

    async def executemany(self, query: str, args_list: Any) -> None:
        for args in args_list:
            self._record(query, tuple(args))
            self.db.route("execute", query, tuple(args))

    async def fetchval(self, query: str, *args: Any) -> Any:
        self._record(query, args)
        rows = self.db.route("fetchval", query, args)
        if not rows:
            return None
        return rows[0].get(_first_column(query), rows[0].get("id"))

    async def fetchrow(self, query: str, *args: Any) -> Any:
        self._record(query, args)
        rows = self.db.route("fetchrow", query, args)
        return rows[0] if rows else None

    async def fetch(self, query: str, *args: Any) -> Any:
        self._record(query, args)
        rows = self.db.route("fetch", query, args)
        return rows if isinstance(rows, list) else []


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class FakePool:
    def __init__(self, db: _FakeDb) -> None:
        self.conn = _FakeConn(db)

    def acquire(self) -> _FakeAcquire:
        # One conn across acquires, so every statement of the request accumulates in order.
        return _FakeAcquire(self.conn)


# =====================================================================================
# The fake planner
# =====================================================================================


class _FakePlanner:
    """Records every call; returns a primed `PlanTemplate`, validates a raw payload, or raises.

    `plan` takes `*args`/`**kwargs` DELIBERATELY. The approved table fixes what the planner
    RETURNS, never the argument list it is called with, and an oracle that pinned a
    signature nobody approved would go red for a reason that has nothing to do with a row.
    `calls` is what makes "the model was never called" a count of exactly 0 (rows 2, 5, 8).
    """

    def __init__(
        self,
        template: Any = None,
        *,
        payload: dict[str, Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._template = template
        self._payload = payload
        self._error = error
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def plan(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        if self._error is not None:
            raise self._error
        if self._payload is not None:
            # Row 2's mechanism: a live planner parses the model's JSON through
            # `PlanTemplate`, so an out-of-range number surfaces HERE, as a ValidationError
            # out of `plan()` — exactly as `messages.parse` would raise it.
            from app.schemas.plans import PlanTemplate

            return PlanTemplate.model_validate(self._payload)
        return self._template if self._template is not None else _template()


# =====================================================================================
# Template builders
# =====================================================================================

# A plausible week: five training days and two rest days, every focus a training label.
_DEFAULT_WEEK: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("push", ("bench press", "overhead press")),
    ("pull", ("barbell row",)),
    ("legs", ("back squat", "deadlift")),
    ("rest", ()),
    ("upper", ("bench press", "barbell row")),
    ("lower", ("back squat",)),
    ("rest", ()),
)


def _item(exercise: str, set_number: int = 1) -> Any:
    from app.schemas.plans import TemplateItem

    return TemplateItem(
        exercise=exercise, set_number=set_number, reps=5, weight_kg=Decimal("61.235")
    )


def _day(focus: str, exercises: tuple[str, ...]) -> Any:
    from app.schemas.plans import TemplateDay

    return TemplateDay(
        focus=focus,
        items=[_item(name, number) for number, name in enumerate(exercises, start=1)],
    )


def _template(
    week: tuple[tuple[str, tuple[str, ...]], ...] = _DEFAULT_WEEK,
    *,
    calories: str = "2400",
    note: str = "Add 2.5 kg to each main lift in weeks 2 and 3; week 4 is a deload.",
) -> Any:
    from app.schemas.plans import PlanTemplate

    return PlanTemplate(
        days=[_day(focus, exercises) for focus, exercises in week],
        calories_target=Decimal(calories),
        protein_g_target=Decimal("180"),
        carbs_g_target=Decimal("250"),
        fat_g_target=Decimal("70"),
        progression_note=note,
    )


def _raw_template(**overrides: Any) -> dict[str, Any]:
    """The same template as a raw dict, so a row can put an out-of-range number in it."""
    payload: dict[str, Any] = {
        "days": [
            {
                "focus": focus,
                "items": [
                    {
                        "exercise": name,
                        "set_number": number,
                        "reps": 5,
                        "weight_kg": "61.235",
                    }
                    for number, name in enumerate(exercises, start=1)
                ],
            }
            for focus, exercises in _DEFAULT_WEEK
        ],
        "calories_target": "2400",
        "protein_g_target": "180",
        "carbs_g_target": "250",
        "fat_g_target": "70",
        "progression_note": "Add 2.5 kg to each main lift in weeks 2 and 3.",
    }
    payload.update(overrides)
    return payload


# =====================================================================================
# Wiring
# =====================================================================================


def _sign_in(
    db: _FakeDb | None = None,
    *,
    planner: _FakePlanner | None = None,
    authenticated: bool = True,
) -> tuple[FakePool, _FakePlanner]:
    """Wire the app: verified caller = USER_ID, fake pool, fake planner.

    The planner override is what keeps CI off the network. `conftest.py`'s autouse
    `no_live_model` fixture does NOT know about `get_planner` (it did not exist when that
    fixture was written), so a route test that forgets this would build the real
    `SonnetPlanner` and dial Anthropic. Every route test here goes through this helper.
    """
    from app.ai.planner import get_planner

    from app.deps import get_pool

    pool = FakePool(db if db is not None else _FakeDb())
    the_planner = planner if planner is not None else _FakePlanner()
    if authenticated:
        app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[get_planner] = lambda: the_planner
    return pool, the_planner


def _freeze(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    """Pin the server clock to `moment`, patched on `app.time` — the module that owns
    `local_today` and is documented as the single source of "today" (#39/#40)."""
    import app.time

    class _Clock:
        @staticmethod
        def now(tz: tzinfo | None = None) -> datetime:
            return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)

    monkeypatch.setattr(app.time, "datetime", _Clock)


def _stored(pool: FakePool, table: str) -> list[dict[str, Any]]:
    return pool.conn.db.tables[table]


def _assert_nothing_stored(pool: FakePool) -> None:
    """Rows 2 and 5: NOTHING landed in any of the three tables.

    Asserted twice over — no INSERT statement was issued against them, AND the tables are
    empty. A status code alone would pass against an implementation that wrote 28 day rows
    and then failed on the 29th.
    """
    for query, _args in pool.conn.calls:
        q = _normalize(query)
        if not q.startswith("insert into"):
            continue
        for table in _PLAN_TABLES:
            assert f"public.{table}" not in q, f"a failed request wrote to public.{table}: {q}"
    for table in _PLAN_TABLES:
        assert _stored(pool, table) == [], f"public.{table} is not empty: {_stored(pool, table)}"


# =====================================================================================
# A. Generation (rows 1, 2, 3, 4, 5, 9, 10)
# =====================================================================================


# AC row 1: POST /plans {weeks:4} with a goal set -> 201 PlanOut; 28 plan_days, each with a
# focus; status='active'.
async def test_row1_four_weeks_is_201_with_28_days_each_with_a_focus(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, datetime(2026, 8, 24, 12, 0, tzinfo=UTC))
    pool, planner = _sign_in(_FakeDb(tz="UTC", goal="cut to 175"))

    resp = await client.post("/plans", json={"weeks": 4})

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "active"
    assert body["weeks"] == 4
    assert body["goal_snapshot"] == "cut to 175"
    assert body["starts_on"] == "2026-08-24"
    assert body["ends_on"] == "2026-09-20"  # starts_on + 4*7 - 1
    assert len(body["days"]) == 28
    assert [d["day_date"] for d in body["days"]] == sorted(d["day_date"] for d in body["days"])
    assert all(d["focus"].strip() for d in body["days"]), body["days"]

    # ...and 28 rows really landed, each with a focus and a week number. The response could
    # be right while the write was wrong; this is the half that says it was stored.
    days = _stored(pool, "plans"), _stored(pool, "plan_days")
    assert len(days[0]) == 1, days[0]
    assert days[0][0]["status"] == "active"
    assert len(days[1]) == 28, days[1]
    assert all(str(row["focus"]).strip() for row in days[1]), days[1]
    assert sorted(int(row["week_number"]) for row in days[1]) == sorted(
        [1] * 7 + [2] * 7 + [3] * 7 + [4] * 7
    )
    assert len({row["day_date"] for row in days[1]}) == 28  # no duplicate dates
    assert len(planner.calls) == 1  # ONE model call for four weeks (the weekly design)


# AC row 2 (the Pydantic half): `calories_target` below 1200 is rejected by the schema
# itself. `ge=1200` is a floor on a number a language model produced; a 900-kcal target is
# not a bug to log, it is advice that must never reach a user.
def test_row2_plan_template_rejects_calories_below_1200() -> None:
    import pydantic
    from app.schemas.plans import PlanTemplate

    with pytest.raises(pydantic.ValidationError):
        PlanTemplate.model_validate(_raw_template(calories_target="900"))

    # ...and the boundary is inclusive, so the floor is 1200 and not 1201.
    assert PlanTemplate.model_validate(_raw_template(calories_target="1200")).calories_target == (
        Decimal("1200")
    )


# AC row 2 (the endpoint half): the model returns 900 -> 503, NOTHING stored in any of the
# three tables. Fails closed: a validation failure inside the planner is a planner failure,
# not a 500 and not a half-written plan.
async def test_row2_a_low_calorie_template_is_503_and_stores_nothing(client: AsyncClient) -> None:
    pool, planner = _sign_in(planner=_FakePlanner(payload=_raw_template(calories_target="900")))

    resp = await client.post("/plans", json={"weeks": 4})

    assert resp.status_code == 503
    assert len(planner.calls) == 1
    _assert_nothing_stored(pool)


# AC row 3: an exercise that is not in the catalog -> THAT ITEM is dropped, its day still
# stores, and the plan is not failed. Mirrors `resolve_exercise` -> None in db/facts.py:
# one unrecognised name must not cost the user the rest of the plan.
async def test_row3_an_unresolvable_exercise_drops_the_item_and_keeps_the_day(
    client: AsyncClient,
) -> None:
    week = (
        ("push", ("bench press", UNRESOLVABLE)),
        ("pull", ("barbell row",)),
        ("legs", ("back squat",)),
        ("rest", ()),
        ("upper", ("bench press",)),
        ("lower", ("back squat",)),
        ("rest", ()),
    )
    pool, _ = _sign_in(planner=_FakePlanner(_template(week)))

    resp = await client.post("/plans", json={"weeks": 1})

    assert resp.status_code == 201  # the plan is NOT failed
    body = resp.json()
    assert len(body["days"]) == 7
    push_day = next(d for d in body["days"] if d["focus"] == "push")
    assert [i["exercise_name"] for i in push_day["items"]] == ["bench press"]  # the item is gone
    assert len(_stored(pool, "plan_days")) == 7  # the day still stored
    # 6 template items in the week, one of them unresolvable -> 5 stored.
    assert len(_stored(pool, "plan_items")) == 5, _stored(pool, "plan_items")
    assert all(row["exercise_id"] is not None for row in _stored(pool, "plan_items"))


# AC row 4: a day where EVERY item is unresolvable stores WITH ZERO ITEMS, and is NOT
# silently converted into a rest day.
#
# This is the row that separates a dropped item from a dropped day, and the difference IS
# the point: an empty training day is a gap the user can see and ask about, while a day
# relabelled "rest" is a lie the app told them about their own program. So the focus must
# come back UNCHANGED — "push", not "rest".
async def test_row4_a_fully_unresolvable_day_stores_empty_and_stays_a_training_day(
    client: AsyncClient,
) -> None:
    week = (
        ("push", (UNRESOLVABLE, f"{UNRESOLVABLE} ii")),
        ("pull", ("barbell row",)),
        ("legs", ("back squat",)),
        ("rest", ()),
        ("upper", ("bench press",)),
        ("lower", ("back squat",)),
        ("rest", ()),
    )
    pool, _ = _sign_in(planner=_FakePlanner(_template(week)))

    resp = await client.post("/plans", json={"weeks": 1})

    assert resp.status_code == 201
    body = resp.json()
    assert len(body["days"]) == 7
    focuses = [d["focus"] for d in body["days"]]
    assert focuses.count("rest") == 2, f"a training day was relabelled as rest: {focuses}"
    push_day = next(d for d in body["days"] if d["focus"] == "push")
    assert push_day["items"] == []  # stored, with zero items
    assert len(_stored(pool, "plan_days")) == 7
    assert len(_stored(pool, "plan_items")) == 3  # row, squat, bench — the push day wrote none


# AC row 5: the model call raises or times out -> 503, nothing stored, retryable. FAILS
# CLOSED, like the coach reply and unlike extraction: a half-written plan the user cannot
# see is worse than no plan.
@pytest.mark.parametrize(
    "error",
    [RuntimeError("vendor exploded"), TimeoutError("the vendor hung")],
    ids=["raises", "times-out"],
)
async def test_row5_a_planner_failure_is_503_and_stores_nothing(
    client: AsyncClient, error: BaseException
) -> None:
    pool, planner = _sign_in(planner=_FakePlanner(error=error))

    resp = await client.post("/plans", json={"weeks": 4})

    assert resp.status_code == 503  # not 500 — this is retryable and the client must know
    assert len(planner.calls) == 1
    _assert_nothing_stored(pool)

    # Retryable means retryable: the same user, on the next request, still gets a plan.
    pool2, _ = _sign_in()
    retry = await client.post("/plans", json={"weeks": 4})
    assert retry.status_code == 201
    assert len(_stored(pool2, "plan_days")) == 28


# AC row 9: `starts_on` is the caller's LOCAL today, never the server's UTC date.
#
# The frozen instant is 2026-08-24 00:30 UTC, which is still 2026-08-23 in Los Angeles. An
# assertion that cannot tell Aug 23 from Aug 24 does not cover this row, so both directions
# are asserted at the SAME instant: the LA user starts on the 23rd, the UTC user on the 24th.
@pytest.mark.parametrize(
    ("tz", "expected_start", "expected_end"),
    [
        ("America/Los_Angeles", "2026-08-23", "2026-09-19"),
        ("UTC", "2026-08-24", "2026-09-20"),
    ],
    ids=["los-angeles", "utc"],
)
async def test_row9_starts_on_is_the_callers_local_today(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tz: str,
    expected_start: str,
    expected_end: str,
) -> None:
    _freeze(monkeypatch, datetime(2026, 8, 24, 0, 30, tzinfo=UTC))
    pool, _ = _sign_in(_FakeDb(tz=tz))

    resp = await client.post("/plans", json={"weeks": 4})

    assert resp.status_code == 201
    body = resp.json()
    assert body["starts_on"] == expected_start
    assert body["ends_on"] == expected_end
    assert min(d["day_date"] for d in body["days"]) == expected_start
    assert str(min(row["day_date"] for row in _stored(pool, "plan_days"))) == expected_start


# AC row 9 (the seatbelt): a NULL profile timezone falls back to UTC rather than crashing —
# the same degradation `local_today` documents, on this endpoint.
async def test_row9_a_null_timezone_falls_back_to_utc(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, datetime(2026, 8, 24, 0, 30, tzinfo=UTC))
    _sign_in(_FakeDb(tz=None))

    resp = await client.post("/plans", json={"weeks": 4})

    assert resp.status_code == 201
    assert resp.json()["starts_on"] == "2026-08-24"


# AC row 10: a user with NO goal still gets a plan — generated from check-in history — and
# `goal_snapshot` is JSON null, NOT the empty string. Toby ruled on this one explicitly: no
# goal is the normal state for a new user, so a 422 would put a profile field on the demo's
# happy path. Null-vs-empty is the same doctrine that produced "peak 0 lb" on /trends.
async def test_row10_no_goal_still_generates_and_snapshots_null(client: AsyncClient) -> None:
    pool, planner = _sign_in(_FakeDb(goal=None))

    resp = await client.post("/plans", json={"weeks": 4})

    assert resp.status_code == 201
    body = resp.json()
    assert body["goal_snapshot"] is None  # null, never "" and never "no goal set"
    assert len(body["days"]) == 28
    assert len(planner.calls) == 1
    assert _stored(pool, "plans")[0]["goal_snapshot"] is None


# =====================================================================================
# B. The pure expansion (rows 6, 7) and the client bound (row 8)
# =====================================================================================


# AC row 6: materialize(template, 2026-08-24, 4) -> 28 dated days; day 1 = starts_on; no
# gaps, no duplicates; week N day K = starts_on + 7(N-1) + (K-1).
def test_row6_materialize_expands_a_week_into_28_dated_days() -> None:
    from app.services.plans import materialize

    starts_on = date(2026, 8, 24)
    days = materialize(_template(), starts_on, 4)

    assert len(days) == 28
    dates = [d.day_date for d in days]
    assert dates[0] == starts_on
    assert len(set(dates)) == 28  # no duplicates
    assert dates == sorted(dates)
    assert all((b - a).days == 1 for a, b in zip(dates, dates[1:], strict=False))  # no gaps

    # The row's own arithmetic, asserted per day rather than as a property of the list.
    for index, day in enumerate(days):
        week_number, day_of_week = divmod(index, 7)
        assert day.week_number == week_number + 1
        assert day.day_date == starts_on + timedelta(days=7 * week_number + day_of_week)
        # ...and each week repeats the template's focus for that weekday.
        assert day.focus == _DEFAULT_WEEK[day_of_week][0]


# AC row 6 (the "pure" half): no clock, no DB, no pool. Structural, so it cannot be true by
# accident — `materialize` takes exactly the three data inputs, and is not a coroutine.
def test_row6_materialize_is_pure_taking_no_pool_and_no_clock() -> None:
    from app.services.plans import materialize

    assert not inspect.iscoroutinefunction(materialize)
    params = inspect.signature(materialize).parameters
    assert set(params) == {"template", "starts_on", "weeks"}, params

    # Determinism, and independence from today: every date comes out of `starts_on`, so a
    # 2019 start can never produce today's date.
    first = materialize(_template(), date(2019, 1, 7), 4)
    second = materialize(_template(), date(2019, 1, 7), 4)
    assert [d.day_date for d in first] == [d.day_date for d in second]
    assert datetime.now(UTC).date() not in {d.day_date for d in first}


# AC row 7: ends_on = starts_on + weeks*7 - 1, which is what satisfies the
# `check (ends_on >= starts_on)` constraint for every legal `weeks`.
@pytest.mark.parametrize("weeks", [1, 2, 3, 4, 5, 6, 7, 8])
def test_row7_last_day_is_starts_on_plus_weeks_times_seven_minus_one(weeks: int) -> None:
    from app.services.plans import materialize

    starts_on = date(2026, 8, 24)
    days = materialize(_template(), starts_on, weeks)

    assert len(days) == weeks * 7
    ends_on = max(d.day_date for d in days)
    assert ends_on == starts_on + timedelta(days=weeks * 7 - 1)
    assert ends_on >= starts_on  # the CHECK constraint, restated at the pure seam


# AC row 8: `weeks` is client input, so it is bounded 1-8. 0, -1 and 53 are 422 — and the
# planner is never called, because an unbounded `weeks` would be a way to spend money and
# write 70,000 rows from one request.
@pytest.mark.parametrize("weeks", [0, -1, 53], ids=["zero", "negative", "fifty-three"])
async def test_row8_out_of_range_weeks_is_422_and_spends_nothing(
    client: AsyncClient, weeks: int
) -> None:
    pool, planner = _sign_in()

    resp = await client.post("/plans", json={"weeks": weeks})

    assert resp.status_code == 422
    assert planner.calls == []  # validation happens before the model, so it costs nothing
    _assert_nothing_stored(pool)


# AC row 8 (the did-not-over-reject control): the bound is 1-8 INCLUSIVE, so both ends are
# accepted. Without this, "422 on 0, -1, 53" is satisfied by an endpoint that rejects
# everything.
@pytest.mark.parametrize("weeks", [1, 8], ids=["lower-bound", "upper-bound"])
async def test_row8_the_bounds_themselves_are_accepted(client: AsyncClient, weeks: int) -> None:
    pool, _ = _sign_in()

    resp = await client.post("/plans", json={"weeks": weeks})

    assert resp.status_code == 201
    assert resp.json()["weeks"] == weeks
    assert len(_stored(pool, "plan_days")) == weeks * 7


# AC row 8 (the same category, non-integer input): `weeks` comes off the wire as JSON, so a
# string is as much a client claim as a number is.
async def test_row8_a_non_integer_weeks_is_422(client: AsyncClient) -> None:
    pool, planner = _sign_in()

    resp = await client.post("/plans", json={"weeks": "four"})

    assert resp.status_code == 422
    assert planner.calls == []
    _assert_nothing_stored(pool)


# =====================================================================================
# C. Isolation — the SQL text of db/plans.py (row 12)
# =====================================================================================
#
# Row 12 is the PR #45 lesson applied in advance. tests/test_data_isolation.py searches a
# whole statement for the WORD `user_id`, so
#     from public.plan_days pd join public.plans p on p.id = pd.plan_id where pd.user_id = $1
# passes it silently — and leaks, because the `plans` side is unfenced. The check-in side
# was the leaking direction PR #45 actually found, and #51 adds two more joins of exactly
# that shape.
#
# So this reads the SOURCE of ONE NAMED FILE, `app/db/plans.py`, and requires a CONTIGUOUS
# `<alias>.user_id = $1` for every user-owned table in every statement.
#
# It names a single file on purpose. #48's row 22 walked a whole directory for a string and
# scanned its own test file, which had to contain the string to assert its absence — a test
# that could never go green. A file scan must name its target.


def _sql_literals(source: str) -> list[str]:
    """Every string literal in a module that looks like SQL, f-strings included.

    Adjacent literals are merged by Python into one constant, which is why db/coach.py
    documents "adjacent string literals, never a runtime concat" as load-bearing rather
    than stylistic: a runtime `+` is analysed as fragments that each look unguarded.
    """
    literals: list[str] = []
    for node in ast.walk(ast.parse(source)):
        text: str | None = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
        verb = re.search(r"\b(select|insert|update|delete)\b", text or "", re.I)
        if text and "public." in text and verb:
            literals.append(text)
    return literals


def _owned_tables_in(query: str) -> list[str]:
    """The user-owned tables this statement names. `exercises` is exempt: it has no owner."""
    return sorted({t for t in re.findall(r"public\.(\w+)", query) if t in _OWNED_TABLES})


def _assert_every_owned_table_is_fenced(query: str) -> None:
    """Row 12: a CONTIGUOUS `<alias>.user_id = $1` for EVERY user-owned table in `query`.

    Three shapes, and the rule differs because the SQL differs:
      * an INSERT binds its owner as a target COLUMN (`insert into ... (user_id, ...)`
        fed from `$1`) — backend rule 3. If it also names a parent table (the rule-4
        `where exists` guard), that parent needs the contiguous filter.
      * a statement naming ONE owned table may write the filter unqualified
        (`where user_id = $1`) — there is nothing to be ambiguous about.
      * a statement naming TWO OR MORE owned tables must qualify EVERY one of them. This
        is the join case, and it is the whole row.

    Self-tested in BOTH directions below (`test_row12_the_guard_rejects_a_one_sided_join`),
    against a one-sided join, a non-contiguous filter, an unowned parameter and the correct
    statement — because a guard nobody has watched reject anything is a decoration.
    """
    q = _normalize(query)
    owned = _owned_tables_in(q)
    assert owned, f"this statement names no user-owned table at all: {q}"

    if q.startswith("insert into"):
        target = re.match(r"insert into public\.(\w+)\s*\(([^)]*)\)", q)
        assert target is not None, f"cannot read the INSERT target of: {q}"
        columns = [c.strip() for c in target.group(2).split(",")]
        assert "user_id" in columns, (
            f"an INSERT into public.{target.group(1)} must set `user_id` from the verified "
            f"caller (backend rule 3) — it has no WHERE to fence it: {q}"
        )
        parents = [t for t in owned if t != target.group(1)]
    else:
        parents = owned

    for table in parents:
        alias = _alias_for(q, table)
        assert alias is not None, f"expected public.{table} in: {q}"
        qualified = f"{alias}.user_id = $1" in q
        unqualified = len(owned) == 1 and re.search(r"(?:^|[\s(])user_id = \$1", q) is not None
        assert qualified or unqualified, (
            f"missing a CONTIGUOUS owner filter on public.{table} (alias {alias!r}) — "
            f"tests/test_data_isolation.py cannot see this, because the word `user_id` "
            f"already appears elsewhere in the statement (PR #45): {q}"
        )


# AC row 12 (the guard on the guard): prove the matcher above actually REJECTS the shape it
# exists to reject. Green from commit #1, deliberately — it needs no implementation, only
# the helper. Same precedent as tests/test_trends.py's row-25 guard test.
def test_row12_the_guard_rejects_a_one_sided_join() -> None:
    fenced_both_sides = (
        "select pd.id, pd.day_date, pd.focus from public.plan_days pd "
        "join public.plans p on p.id = pd.plan_id "
        "where pd.user_id = $1 and p.user_id = $1 and p.status = 'active' "
        "order by pd.day_date"
    )
    _assert_every_owned_table_is_fenced(fenced_both_sides)  # must NOT raise

    # THE LEAK. `user_id` appears, so test_data_isolation.py's regex is satisfied, but the
    # `plans` side of the join is wide open — anyone's plan can supply the days.
    plans_side_open = (
        "select pd.id, pd.day_date from public.plan_days pd "
        "join public.plans p on p.id = pd.plan_id "
        "where pd.user_id = $1 and p.status = 'active'"
    )
    with pytest.raises(AssertionError):
        _assert_every_owned_table_is_fenced(plans_side_open)

    # The mirror image: the child side unfenced.
    days_side_open = (
        "select pd.id, pd.day_date from public.plan_days pd "
        "join public.plans p on p.id = pd.plan_id "
        "where p.user_id = $1 and p.status = 'active'"
    )
    with pytest.raises(AssertionError):
        _assert_every_owned_table_is_fenced(days_side_open)

    # A three-table join with the middle table unfenced — the shape #51 actually adds.
    items_join_middle_open = (
        "select pi.id, pi.reps from public.plan_items pi "
        "join public.plan_days pd on pd.id = pi.plan_day_id "
        "join public.plans p on p.id = pd.plan_id "
        "where pi.user_id = $1 and p.user_id = $1"
    )
    with pytest.raises(AssertionError):
        _assert_every_owned_table_is_fenced(items_join_middle_open)

    # Present but NOT CONTIGUOUS — both words are in the statement and neither fences.
    non_contiguous = (
        "select pd.id from public.plan_days pd join public.plans p on p.id = pd.plan_id "
        "where pd.user_id = p.user_id and p.status = 'active'"
    )
    with pytest.raises(AssertionError):
        _assert_every_owned_table_is_fenced(non_contiguous)

    # Single-table statements may write the filter unqualified; a single-table statement
    # with NO filter at all must still be rejected.
    _assert_every_owned_table_is_fenced(
        "select id, status from public.plans where user_id = $1 and status = 'active'"
    )
    with pytest.raises(AssertionError):
        _assert_every_owned_table_is_fenced("select id, status from public.plans where id = $1")

    # An INSERT is fenced by its target column, not by a WHERE...
    _assert_every_owned_table_is_fenced(
        "insert into public.plans (user_id, starts_on, ends_on, weeks) "
        "values ($1, $2, $3, $4) returning id"
    )
    # ...and one that forgets `user_id` binds the row to nobody (backend rule 3).
    with pytest.raises(AssertionError):
        _assert_every_owned_table_is_fenced(
            "insert into public.plans (starts_on, ends_on, weeks) values ($1, $2, $3) returning id"
        )
    # A guarded child write must fence the PARENT it reads (backend rule 4).
    _assert_every_owned_table_is_fenced(
        "insert into public.plan_items (user_id, plan_day_id, exercise_id, set_number, reps) "
        "select $1, $2, $3, $4, $5 "
        "where exists (select 1 from public.plan_days pd where pd.id = $2 and pd.user_id = $1) "
        "returning id"
    )
    with pytest.raises(AssertionError):
        _assert_every_owned_table_is_fenced(
            "insert into public.plan_items (user_id, plan_day_id, exercise_id, set_number, reps) "
            "select $1, $2, $3, $4, $5 "
            "where exists (select 1 from public.plan_days pd where pd.id = $2) returning id"
        )

    # The shared catalog is exempt and must not be required to carry an owner.
    _assert_every_owned_table_is_fenced(
        "select pi.id, e.name from public.plan_items pi "
        "join public.exercises e on e.id = pi.exercise_id where pi.user_id = $1"
    )

    # And the helper must not depend on the author picking the aliases `pd` / `p`.
    _assert_every_owned_table_is_fenced(
        "select d.id from public.plan_days as d join public.plans as pl on pl.id = d.plan_id "
        "where d.user_id = $1 and pl.user_id = $1"
    )


# AC row 12: EVERY statement in app/db/plans.py carries a contiguous `<alias>.user_id = $1`
# on both sides of every join.
def test_row12_every_statement_in_db_plans_is_owner_scoped_on_every_side() -> None:
    module = Path(__file__).resolve().parents[1] / "app" / "db" / "plans.py"
    assert module.exists(), (
        f"{module} does not exist yet — this is the CORRECT oracle-time failure for row 12"
    )

    statements = _sql_literals(module.read_text())
    # A scan that silently matched nothing passes every assertion below while proving
    # nothing. `db/plans.py` has to insert into three tables and read them back, so a
    # plausible floor is well above zero.
    assert len(statements) >= 3, (
        f"only {len(statements)} SQL statements found in {module.name}; the row-12 scan is "
        "vacuous — either the parser broke or the file does not do what the feature needs"
    )
    touched = {t for s in statements for t in _owned_tables_in(_normalize(s))}
    assert set(_PLAN_TABLES) <= touched, f"db/plans.py never touches {set(_PLAN_TABLES) - touched}"

    for statement in statements:
        _assert_every_owned_table_is_fenced(statement)


# =====================================================================================
# D. Diet actuals (row 20) — NO TEST HERE. READ THIS BEFORE ASSUMING ONE IS MISSING.
# =====================================================================================
#
# 🔓 AMENDED BY AMENDMENT 3 ON ISSUE #51, APPROVED BEFORE ANY IMPLEMENTATION EXISTED.
#
#   amendment: https://github.com/tobyliu2004/coach-bill/issues/51#issuecomment-5389340006
#
# ⚠️ DIRECTION: WIDENING — THIS CAN NEWLY PASS. That is the dangerous direction, and naming
# it is the whole reason this block exists.
#
# The oracle commit originally carried `test_row20_three_meals_in_a_day_are_summed_not_
# latest_wins`, asserting the sum through a `GET /diet` endpoint. `test-author` flagged
# that row 20 names no seam — it says WHAT must be true ("three meals logged in a day ->
# summed, not latest-wins") and never says which endpoint or function makes it true — and
# `GET /diet` was a READING, not an approved expectation. Toby ruled the other way: **/diet
# adds no endpoint.** The screen composes `GET /trends?days=1` (nutrition) and
# `GET /plans/current` (targets), which the approved design says in three places.
#
# So the removal of that test means an implementation with NO `GET /diet` now PASSES this
# suite where it would previously have FAILED. Say it plainly rather than calling this a
# "move": an assertion left this file and nothing in this file replaced it.
#
# Why that is defensible, and exactly where the property now lives:
#
#   * The property is not going untested. `schemas/trends.py`'s `NutritionPoint` already
#     carries {calories, protein_g, carbs_g, fat_g} SUMMED per day, in a sparse series, and
#     the sum is already proven against a real Postgres in CI's `rls-tests` job by
#     `backend/tests/test_trends.py::test_three_nutrition_rows_on_one_day_are_summed_not_latest`.
#     That is a stronger oracle for row 20 than anything this tier could have been: a fake
#     pool cannot grade a SQL `sum()` without priming the answer it then asserts.
#   * That test is shipped, frozen, and untouched by this branch.
#   * ⚠️ IF ANYONE EVER DELETES THAT TEST, ROW 20 BECOMES GENUINELY UNCOVERED. There is no
#     second copy. A future change to `db/trends.py`'s nutrition aggregate that also removes
#     its test would silently take row 20 with it, and nothing in this file would go red.
#
# Row 20's frontend half — the diet screen consuming the already-summed `NutritionPoint`
# for TODAY out of the sparse series, never summing client-side and never taking the latest
# point regardless of date — is written in `frontend/src/lib/plan.test.ts`.


# =====================================================================================
# E. The frozen context builder (row 26)
# =====================================================================================


# AC row 26: `build_context(...)` called WITHOUT the new `plan` kwarg produces output that
# is BYTE-IDENTICAL to today's.
#
# This is what keeps #21's and #48's frozen oracles green without being touched, so it is
# asserted against a STORED STRING captured from the shipped function on 2026-08-23 — not
# against a second call to the same function, which would be true of any implementation
# including a broken one.
#
# It also fails if a REQUIRED kwarg is added: the call below passes exactly today's five
# arguments, so a mandatory sixth parameter raises TypeError here. `plan` must therefore
# arrive keyword-only WITH a default.
_BUILD_CONTEXT_TODAY = (
    "GOAL\ncut to 175\n\n"
    "WEIGHT UNIT\nShow weights in lb.\n\n"
    "RECENT CHECK-INS (newest first)\n"
    "- 2024-01-14: bench 135 4x8\n\n"
    "TRENDS (2024-01-01 to 2024-01-14)\n"
    "- 2024-01-14: 3200 kg total volume\n"
    "- 2024-01-14: 1 bodyweight sets, 20 reps (no external load to measure)\n"
    "- bench press: 4 sets, 32 reps, heaviest 61.235 kg\n"
    "- 2024-01-14: slept 7.5h, quality 3/5\n"
    "- 2024-01-14: bodyweight 80 kg\n"
    "- 2024-01-14: 2100 kcal, 150g protein, 200g carbs, 70g fat\n\n"
    "YOUR LAST FEW REPLIES (do not repeat these)\n"
    "- yesterday's reply"
)


def _frozen_context_inputs() -> dict[str, Any]:
    from app.schemas.check_ins import CheckInOut
    from app.schemas.trends import (
        BodyweightPoint,
        ExerciseSummary,
        NutritionPoint,
        SleepPoint,
        TrendsOut,
        VolumePoint,
    )

    trends = TrendsOut(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 14),
        volume=[
            VolumePoint(
                date=date(2024, 1, 14),
                volume_kg=Decimal("3200"),
                bodyweight_sets=1,
                bodyweight_reps=20,
            )
        ],
        exercises=[
            ExerciseSummary(
                name="bench press",
                sets=4,
                reps=32,
                volume_kg=Decimal("3200"),
                heaviest_kg=Decimal("61.235"),
            )
        ],
        sleep=[SleepPoint(date=date(2024, 1, 14), hours=Decimal("7.5"), quality=3)],
        bodyweight=[BodyweightPoint(date=date(2024, 1, 14), weight_kg=Decimal("80"))],
        nutrition=[
            NutritionPoint(
                date=date(2024, 1, 14),
                calories=Decimal("2100"),
                protein_g=Decimal("150"),
                carbs_g=Decimal("200"),
                fat_g=Decimal("70"),
            )
        ],
    )
    check_in = CheckInOut(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        raw_text="bench 135 4x8",
        source="text",
        entry_date=date(2024, 1, 14),
        created_at=datetime(2024, 1, 14, 12, 0, tzinfo=UTC),
        extraction_status="done",
    )
    return {
        "goal": "cut to 175",
        "weight_unit": "lb",
        "trends": trends,
        "check_ins": [check_in],
        "recent_replies": ["yesterday's reply"],
    }


def test_row26_build_context_without_the_plan_kwarg_is_byte_identical_to_today() -> None:
    from app.services.coach import build_context

    assert build_context(**_frozen_context_inputs()) == _BUILD_CONTEXT_TODAY


# AC row 26 (the structural half): `plan` arrives KEYWORD-ONLY and OPTIONAL. A required
# parameter would break every existing caller and every frozen row 21 test; a positional
# one would silently re-order the five arguments those tests pass.
def test_row26_the_new_plan_parameter_is_keyword_only_and_optional() -> None:
    from app.services.coach import build_context

    params = inspect.signature(build_context).parameters
    assert set(params) == {"goal", "weight_unit", "trends", "check_ins", "recent_replies", "plan"}
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params.values())
    assert params["plan"].default is None
    # Passing it explicitly as None must be the same as not passing it at all — otherwise
    # "byte-identical to today's" would depend on which of two ways the caller wrote it.
    assert build_context(**_frozen_context_inputs(), plan=None) == _BUILD_CONTEXT_TODAY


# =====================================================================================
# F. Auth negative paths — `.claude/rules/backend.md`, not a table row
# =====================================================================================
#
# The rules file is acceptance criteria whether or not the table restates it, and #51's
# table has no auth row. The full negative matrix (wrong signature, algorithm confusion,
# wrong issuer, wrong audience) exercises the SHARED `get_current_user_id` dependency and
# is proven exhaustively in tests/test_auth.py; duplicating it would test the dependency
# twice and these endpoints no better. What is proven HERE is that the new endpoints are
# actually behind it — the failure mode a new router introduces.


@pytest.mark.parametrize(
    ("method", "path"),
    [("post", "/plans"), ("get", "/plans/current")],
    ids=["post-plans", "get-current"],
)
async def test_the_new_endpoints_are_401_without_a_token(
    client: AsyncClient, method: str, path: str
) -> None:
    from types import SimpleNamespace

    from app.ai.planner import get_planner

    from app.auth import get_jwks_client
    from app.deps import get_pool

    pool = FakePool(_FakeDb())
    planner = _FakePlanner()
    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[get_planner] = lambda: planner
    # The real dependency, with a stub JWKS client so an unauthenticated request can never
    # reach the network (copied in shape from tests/test_trends.py's auth section).
    app.dependency_overrides[get_jwks_client] = lambda: SimpleNamespace()
    app.dependency_overrides.pop(get_current_user_id, None)

    resp = await client.request(method, path, json={"weeks": 4} if method == "post" else None)

    assert resp.status_code == 401
    assert planner.calls == []  # an anonymous caller cannot spend a model call
    _assert_nothing_stored(pool)


# =====================================================================================
# G. Machinery self-tests — the fake's INSERT router (see this module's docstring)
# =====================================================================================


# Machinery for rows 1/3/4/9/10: the router that turns an INSERT into stored rows must
# actually understand the shapes it claims to. Without this, "28 plan_days landed" could be
# a parser bug in either direction.
def test_the_fake_insert_router_understands_the_bulk_shapes() -> None:
    plan_id = uuid.uuid4()
    dates = [date(2026, 8, 24), date(2026, 8, 25)]

    # 1. one `values` tuple (the executemany shape)
    table, rows = _parse_insert(
        "insert into public.plan_days (user_id, plan_id, day_date, week_number, focus) "
        "values ($1, $2, $3, $4, $5)",
        (USER_ID, plan_id, dates[0], 1, "push"),
    )
    assert table == "plan_days"
    assert rows == [
        {
            "user_id": USER_ID,
            "plan_id": plan_id,
            "day_date": dates[0],
            "week_number": 1,
            "focus": "push",
        }
    ]

    # 2. many `values` tuples in one statement
    _, many = _parse_insert(
        "insert into public.plan_days (user_id, plan_id, day_date) "
        "values ($1, $2, $3), ($1, $2, $4)",
        (USER_ID, plan_id, dates[0], dates[1]),
    )
    assert [r["day_date"] for r in many] == dates

    # 3. the guarded-write `select ... where exists (...)` house style (db/facts.py)
    _, guarded = _parse_insert(
        "insert into public.plan_items (user_id, plan_day_id, exercise_id, set_number, reps) "
        "select $1, $2, $3, $4, $5 "
        "where exists (select 1 from public.plan_days where id = $2 and user_id = $1) "
        "returning id",
        (USER_ID, plan_id, CATALOG["bench press"], 1, 5),
    )
    assert guarded == [
        {
            "user_id": USER_ID,
            "plan_day_id": plan_id,
            "exercise_id": CATALOG["bench press"],
            "set_number": 1,
            "reps": 5,
        }
    ]

    # 4. the bulk `unnest` shapes, both spellings
    _, unnested = _parse_insert(
        "insert into public.plan_days (user_id, plan_id, day_date, week_number, focus) "
        "select $1, $2, unnest($3::date[]), unnest($4::int[]), unnest($5::text[])",
        (USER_ID, plan_id, dates, [1, 1], ["push", "pull"]),
    )
    expected = [(dates[0], "push"), (dates[1], "pull")]
    assert [(r["day_date"], r["focus"]) for r in unnested] == expected

    _, aliased = _parse_insert(
        "insert into public.plan_days (user_id, plan_id, day_date, focus) "
        "select $1, $2, u.day_date, u.focus "
        "from unnest($3::date[], $4::text[]) as u(day_date, focus)",
        (USER_ID, plan_id, dates, ["push", "pull"]),
    )
    assert [(r["day_date"], r["focus"]) for r in aliased] == expected

    # ...and a shape it cannot read is LOUD, never a silently wrong answer.
    with pytest.raises(_FakeSqlError):
        _parse_insert("insert into public.plan_days select * from somewhere_else", ())
