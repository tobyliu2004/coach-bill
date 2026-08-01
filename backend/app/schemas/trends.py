"""Pydantic shapes for GET /trends — the computed rollup of the facts #19 extracts.

The frontend `Trends` interface (frontend/src/lib/api.ts) mirrors `TrendsOut` — keep them
in sync.

Every measure is a `Decimal`, which Pydantic serializes to a JSON *string*, exactly as the
per-check-in shapes already do (schemas/check_ins.py): JSON's only number type is a float,
and a tonnage total is not something a parser gets to round. The browser parses these once,
deliberately, in a tested pure function (`lib/trends.ts`).

These are OUTPUT models, so there is no `extra="forbid"` — nothing here is ever parsed from
a client request. The one input this feature takes is `?days=`, and that is validated at the
route.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class VolumePoint(BaseModel):
    """One day's training volume.

    `volume_kg` is NULL, never 0, on a day of purely unweighted work. That distinction is
    the whole of AC row 11: a day of pushups did not have *zero* tonnage, it had none that
    can be measured, and reporting 0 would draw a chart that says the user did nothing.
    Bodyweight work is therefore counted on its own two axes rather than imputed — inventing
    a load from `bodyweight_entries` would also silently rewrite every past day each time
    the user steps on a scale.
    """

    date: date
    volume_kg: Decimal | None
    bodyweight_sets: int
    bodyweight_reps: int


class ExerciseSummary(BaseModel):
    """One movement's totals across the whole window.

    ASYMMETRIC ON PURPOSE (AC row 21): `sets` and `reps` count every set, while `volume_kg`
    and `heaviest_kg` count only the weighted ones. "How much did I do?" includes the
    pushups; "how much did I lift?" cannot.

    `name` is the CANONICAL catalog name. Aliases were already resolved at write time (#19
    stores `coalesce(canonical_id, id)`), so a window mixing "curls" and "barbell curl" is
    one row here, not two — the trend must not split one movement in half.
    """

    name: str
    sets: int
    reps: int
    volume_kg: Decimal | None
    heaviest_kg: Decimal | None


class SleepPoint(BaseModel):
    """One night's sleep — the LATEST row for that day (AC row 15).

    Sleep is a one-per-day fact, so a second row is a correction ("actually it was 8h"), not
    a second night. Summing would report 15 hours; averaging would report a number the user
    never said.
    """

    date: date
    hours: Decimal
    quality: int | None


class BodyweightPoint(BaseModel):
    """One day's bodyweight — latest row wins, for the same reason sleep does."""

    date: date
    weight_kg: Decimal


class NutritionPoint(BaseModel):
    """One day's food, SUMMED (AC row 17).

    The one place the "latest row wins" rule is wrong. Three rows a day is the normal shape
    here — breakfast, lunch, dinner, often logged across three separate check-ins — so
    latest-wins would discard two meals and report dinner as the day's intake. Plausible,
    and wrong by 1100 calories.
    """

    date: date
    calories: Decimal
    protein_g: Decimal
    carbs_g: Decimal
    fat_g: Decimal


class TrendsOut(BaseModel):
    """Everything the dashboard needs, in one response.

    ONE composite endpoint rather than five per-metric ones: the screen wants all of it at
    once and so will Coach Bill (#21), and five round trips would let the series disagree
    about which window they describe.

    `start_date`/`end_date` are echoed back deliberately. The client never recomputes
    "today" — it lays the chart's axis out from the window the SERVER resolved, which is the
    drift issue #40 is about. They also survive an empty result: a screen has to be able to
    say *which* window is empty, and "you have no data" to someone who took a month off is
    the same lie /history already avoids.

    The series are SPARSE — a day with nothing logged is absent, not a row of nulls. That is
    `groupByDay`'s existing doctrine (frontend lib/history.ts): a rendered empty day claims
    you logged nothing, when the truth is you didn't log.
    """

    start_date: date
    end_date: date
    volume: list[VolumePoint] = Field(default_factory=list)
    exercises: list[ExerciseSummary] = Field(default_factory=list)
    sleep: list[SleepPoint] = Field(default_factory=list)
    bodyweight: list[BodyweightPoint] = Field(default_factory=list)
    nutrition: list[NutritionPoint] = Field(default_factory=list)
