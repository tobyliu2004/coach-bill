"""Pydantic shapes for check-ins (POST/GET /check-ins).

The frontend `CheckIn` interface (frontend/src/lib/api.ts) mirrors `CheckInOut` — keep
them in sync.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Strip surrounding whitespace, reject empty, cap length — mirrors the `_Goal` constraint
# in schemas/profiles.py. This is input hygiene, not fitness/safety validation (that's a
# separate future issue).
_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class CheckInCreate(BaseModel):
    """POST /check-ins body: the client sends only the text.

    `source` and `user_id` are server-owned — `extra="forbid"` makes any other key
    (including a smuggled `user_id`) a 422, so the client cannot even name an owner.
    """

    model_config = ConfigDict(extra="forbid")

    text: _Text


class WorkoutSetOut(BaseModel):
    """One stored `workout_sets` row. `weight_kg` is null for bodyweight moves — never 0."""

    exercise_name: str
    set_number: int
    reps: int
    weight_kg: Decimal | None


class NutritionEntryOut(BaseModel):
    """One stored `nutrition_entries` row. Macros are AI-estimated today (known debt)."""

    description: str
    calories: Decimal
    protein_g: Decimal
    carbs_g: Decimal
    fat_g: Decimal
    meal: Literal["breakfast", "lunch", "dinner", "snack"] | None


class SleepEntryOut(BaseModel):
    """One stored `sleep_entries` row. `quality` is null unless the user actually rated it."""

    hours: Decimal
    quality: int | None


class BodyweightEntryOut(BaseModel):
    """One stored `bodyweight_entries` row."""

    weight_kg: Decimal


class CheckInFacts(BaseModel):
    """What extraction pulled out of one check-in, as stored (weights in canonical kg).

    All four lists empty is a normal, successful outcome — see `extraction_status` below.
    """

    sets: list[WorkoutSetOut] = Field(default_factory=list)
    nutrition: list[NutritionEntryOut] = Field(default_factory=list)
    sleep: list[SleepEntryOut] = Field(default_factory=list)
    bodyweight: list[BodyweightEntryOut] = Field(default_factory=list)


class CheckInOut(BaseModel):
    """A `check_ins` row as returned to its owner, with its derived facts bundled in.

    Facts are bundled rather than fetched separately so the list screen renders in one
    round trip and can never show a check-in without the facts that belong to it.
    """

    id: UUID
    raw_text: str
    source: Literal["voice", "text"]
    entry_date: date
    created_at: datetime

    # 'pending' : saved, extraction unfinished (also what a crash leaves behind)
    # 'done'    : extraction ran and stored everything it found — INCLUDING when it found
    #             nothing. "No fitness in this text" is success, not failure.
    # 'partial' : a fact was found but had to be dropped (a rejected exercise name). This
    #             is the only thing 'partial' means, so the UI's warning never cries wolf.
    # 'failed'  : extraction itself broke. The raw text is intact regardless.
    extraction_status: Literal["pending", "done", "partial", "failed"]
    facts: CheckInFacts = Field(default_factory=CheckInFacts)
