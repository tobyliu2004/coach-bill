"""Pydantic shapes for the plan feature (issue #51).

Three families in one module, and the split matters:

  * `PlanTemplate` and friends are what the MODEL returns — one week, not 28 days. They are
    the boundary where an untrusted generation becomes a typed value, and every bound on
    them is a bound on advice, not on a database row.
  * `PlanCreate` is what the CLIENT sends. Exactly one number, bounded.
  * `PlanOut` / `PlanDayOut` / `PlanItemOut` are what the API returns.

Every measure is a `Decimal`, which Pydantic serializes to a JSON *string*, exactly as
`schemas/trends.py` and `schemas/check_ins.py` already do: JSON's only number type is a
float, and a calorie target is not something a parser gets to round. The browser parses
these once, deliberately, in a tested pure function (`lib/plan.ts` via `parseNumeric`).
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# The model writes ONE WEEK and `materialize` repeats it. Seven is not a tunable: it is what
# makes "week N day K" arithmetic, and it is why one Sonnet call covers four weeks instead
# of a 4-5k-token generation the model pads with filler.
DAYS_PER_WEEK = 7

# Row 8's bound, declared once and reused by the request schema. `weeks` is CLIENT input;
# unbounded, `weeks: 10000` is 70,000 `plan_days` from a single request.
MIN_WEEKS = 1
MAX_WEEKS = 8

# ⚠️ Row 2's floor, and the SECOND of three locks on the same rule. `COACH_SYSTEM_PROMPT`
# forbids naming a target below this, and the `plans` CHECK constraint refuses to store one.
# This is the lock that catches a model which ignored the prompt, inside the boundary and
# before anything is written — which is what makes row 2 "503, nothing stored" rather than
# "stored, then noticed".
MIN_CALORIES_TARGET = Decimal(1200)


class TemplateItem(BaseModel):
    """One PLANNED set, as the model describes it.

    One object per set, never "3x8" in a string — the same shape `schemas/extraction.py`
    uses for a logged set, and for the same reason: `plan_items` mirrors `workout_sets`, so
    planned-vs-actual is a SQL join instead of a parser nobody wants to write later.
    """

    # Free text on purpose: this is the model's word for a movement, and it is looked up in
    # the seeded catalog by `resolve_exercise`. A name the catalog does not know resolves to
    # None and the ITEM is dropped, the day surviving (row 3) — never invented, never
    # inserted. `exercises` has no write path at all (#19).
    exercise: str
    # ⚠️ BOUNDED ABOVE, AND THE UPPER BOUND IS THE LOAD-BEARING HALF.
    # Both columns are `smallint`. Unbounded here, a model returning `reps: 40000` passes
    # validation, escapes the `try/except -> PlannerUnavailable` in `services.create_plan`
    # (which wraps ONLY the model call), and then dies inside `insert_plan` as an asyncpg
    # encode error no handler catches — a 500 after the call was already paid for, where
    # row 2's design says a bad template is a 503 with nothing stored. The boundary only
    # holds if everything the model can send is checked AT the boundary, not just the
    # calorie floor. 200 is well past any real prescription and far inside smallint.
    set_number: int = Field(ge=1, le=200)
    reps: int = Field(ge=0, le=200)
    # NULLABLE, never 0. A bodyweight movement has no external load; 0 would tell the user
    # they are planned to lift nothing. Same rule as `workout_sets.weight_kg` and the same
    # null-vs-zero doctrine whose violation shipped as "peak 0 lb" on /trends.
    weight_kg: Decimal | None = Field(default=None, ge=0)


class TemplateDay(BaseModel):
    """One day of the week-long template."""

    # NOT NULL and never empty — row L4 checks the model honours that, and row 4 depends on
    # it: a day whose every item was dropped still stores WITH ITS FOCUS, so "we planned
    # push and resolved nothing" stays distinguishable from "we planned rest".
    # `min_length=1` mirrors the DB CHECK. Two locks, the doctrine this feature already
    # applies to the calorie floor: Pydantic gives a 503 with nothing stored, the CHECK
    # makes it impossible even if this boundary is ever bypassed.
    focus: str = Field(min_length=1)
    items: list[TemplateItem] = Field(default_factory=list)


class PlanTemplate(BaseModel):
    """What the planner returns: one week of training plus the daily diet targets.

    ⚠️ THIS IS THE VALIDATION BOUNDARY FOR AN UNTRUSTED GENERATION. `messages.parse` hands
    back an instance of this class, so a model that returned `calories_target: 900` raises
    `ValidationError` HERE — inside `Planner.plan`, before `create_plan` has written
    anything. That is the mechanism behind row 2's "503, nothing stored": the failure
    happens before the transaction, so there is no cleanup path to get wrong.
    """

    days: list[TemplateDay] = Field(min_length=DAYS_PER_WEEK, max_length=DAYS_PER_WEEK)

    calories_target: Decimal = Field(ge=MIN_CALORIES_TARGET)
    protein_g_target: Decimal = Field(ge=0)
    carbs_g_target: Decimal = Field(ge=0)
    fat_g_target: Decimal = Field(ge=0)

    # One line the user reads on the plan screen. Row L2 checks it describes actual
    # progression across weeks rather than restating week 1.
    progression_note: str = Field(min_length=1)


class PlanCreate(BaseModel):
    """`POST /plans` — the only thing the client gets to choose.

    `extra="forbid"` so a payload carrying `user_id`, `starts_on` or `calories_target` is a
    422 rather than a silently-ignored field that looks like it worked. Ownership comes from
    `UserIdDep` and the dates come from the server; nothing here is negotiable.
    """

    model_config = ConfigDict(extra="forbid")

    weeks: int = Field(ge=MIN_WEEKS, le=MAX_WEEKS)


class PlanItemOut(BaseModel):
    """One planned set, as the API returns it."""

    id: UUID
    exercise_id: UUID
    # The CANONICAL catalog name, not the model's wording. Aliases were resolved at write
    # time (`resolve_exercise` returns `coalesce(canonical_id, id)`), so a plan that said
    # "curls" reads back as "barbell curl" — one movement, not two.
    exercise_name: str
    set_number: int
    reps: int
    weight_kg: Decimal | None


class PlanDayOut(BaseModel):
    """One dated day of the materialized plan."""

    id: UUID
    day_date: date
    week_number: int
    focus: str
    # Row 13. Computed by joining THIS day's date to the CALLER'S OWN workouts — never
    # stored, so it cannot go stale, and never derived from anyone else's rows.
    logged: bool
    items: list[PlanItemOut] = Field(default_factory=list)


class PlanOut(BaseModel):
    """The whole active plan in one response — header, targets and every dated day.

    One composite payload rather than a day-by-day endpoint, the same call `/trends` made:
    the screen renders a 28-day calendar at once, and four round trips would let the days
    disagree about which plan they belong to.
    """

    id: UUID
    status: str
    starts_on: date
    ends_on: date
    weeks: int
    # Row 10: NULL is a legitimate value. No goal is the NORMAL state for a new user, and a
    # plan generated from history alone is a correct answer — not `""`, not "no goal set".
    goal_snapshot: str | None
    progression_note: str
    calories_target: Decimal
    protein_g_target: Decimal
    carbs_g_target: Decimal
    fat_g_target: Decimal
    created_at: datetime
    days: list[PlanDayOut] = Field(default_factory=list)
