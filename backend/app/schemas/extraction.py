"""The shape of what the model hands back — and the first place we stop trusting it.

An LLM's output is untrusted input, exactly like a request body. It is *less* predictable
than one: a request body is written by a client we can at least reason about, whereas this
is generated text that can be wrong, confused, or steered by whatever the user typed into
their check-in. So every constraint the database CHECKs enforce is mirrored here, and junk
(reps = -1, 30 hours of sleep) is rejected at the boundary rather than at the DB (AC row 10).

Why mirror instead of just letting Postgres reject it: a CHECK violation arrives as an
opaque `asyncpg.CheckViolationError` mid-write, after some rows may already have gone in.
Validating up front means one bad number fails the extraction cleanly, the check-in is
marked 'failed', and the raw text is untouched — which is the whole promise of AC row 9/10.

Units: `weight` here is in **the user's profile unit** (lb or kg), NOT converted. Conversion
to canonical kg happens in services/extraction.py, because only the profile knows the unit —
the text never says. Keeping the boundary in the user's unit is what makes AC rows 1 and 2
distinguishable: identical model output, different stored kg.
"""

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.check_ins import Meal

# The exercise name the model read out of the text. Length only — the real gate is
# `public.resolve_exercise` (the guarded door), which normalizes and validates the charset
# before anything reaches the shared `exercises` catalog. Doing charset validation here too
# would be a second, drifting copy of that rule; the door is the single source of truth.
_ExerciseName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]

_Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class ExtractedSet(BaseModel):
    """One set of one exercise — mirrors `public.workout_sets`."""

    model_config = ConfigDict(extra="forbid")

    exercise_name: _ExerciseName
    # workout_sets.set_number: `check (set_number > 0)`
    set_number: Annotated[int, Field(gt=0, le=100)]
    # workout_sets.reps: `check (reps >= 0)`
    reps: Annotated[int, Field(ge=0, le=1000)]
    # workout_sets.weight_kg: `check (weight_kg >= 0)`, nullable — NULL (never 0) is what a
    # bodyweight move stores (AC row 3); 0 would drag a strength average toward zero.
    weight: Annotated[Decimal, Field(ge=0, le=1000)] | None = None


class ExtractedNutrition(BaseModel):
    """One food item and its macros — mirrors `public.nutrition_entries`.

    All four macros are NOT NULL in the schema, so this boundary can never say "I don't
    know" (AC row 5) — they are required fields with no default. Today the numbers are the
    model's estimate; `get_nutrition` in ai/extractor.py is the seam where a real food
    database (USDA FoodData Central) replaces that guess without touching any caller.
    """

    model_config = ConfigDict(extra="forbid")

    description: _Description
    # nutrition_entries: `check (calories >= 0)` etc. — all four columns, all NOT NULL.
    calories: Annotated[Decimal, Field(ge=0, le=20000)]
    protein_g: Annotated[Decimal, Field(ge=0, le=2000)]
    carbs_g: Annotated[Decimal, Field(ge=0, le=2000)]
    fat_g: Annotated[Decimal, Field(ge=0, le=2000)]
    meal: Meal | None = None


class ExtractedSleep(BaseModel):
    """One night's sleep — mirrors `public.sleep_entries`."""

    model_config = ConfigDict(extra="forbid")

    # sleep_entries.hours: `check (hours >= 0 and hours <= 24)`
    hours: Annotated[Decimal, Field(ge=0, le=24)]
    # sleep_entries.quality: `check (quality between 1 and 5)`, nullable — stays None
    # unless the user actually said so. Inventing a 3/5 is fabrication (AC row 4).
    quality: Annotated[int, Field(ge=1, le=5)] | None = None


class ExtractedBodyweight(BaseModel):
    """One bodyweight measurement — mirrors `public.bodyweight_entries`."""

    model_config = ConfigDict(extra="forbid")

    # bodyweight_entries.weight_kg: `check (weight_kg > 0)` — STRICTLY positive, unlike
    # workout_sets.weight_kg (`>= 0`). In the profile's unit; converted in the service.
    weight: Annotated[Decimal, Field(gt=0, le=1000)]


class ExtractedFacts(BaseModel):
    """Everything the model found in one check-in. Empty lists are the normal "nothing to
    extract" answer (AC row 11) — that is success, not failure.
    """

    model_config = ConfigDict(extra="forbid")

    sets: list[ExtractedSet] = Field(default_factory=list)
    nutrition: list[ExtractedNutrition] = Field(default_factory=list)
    sleep: list[ExtractedSleep] = Field(default_factory=list)
    bodyweight: list[ExtractedBodyweight] = Field(default_factory=list)

    def is_empty(self) -> bool:
        """True when the model found no facts at all."""
        return not (self.sets or self.nutrition or self.sleep or self.bodyweight)
