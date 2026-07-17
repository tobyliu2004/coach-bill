"""Extraction business logic: model output -> canonical, stored facts.

The three judgment calls all live here, not in db/ and not in the route:
  * units   — the model reports the number as written; only the PROFILE knows lb vs kg
  * the catalog — exercise names are looked up in the seeded `exercises` catalog
    (db/facts.py); an unknown name drops its sets and marks the check-in 'partial'
  * status  — what 'done' / 'partial' / 'failed' mean (see `_STATUS_*` below)
"""

from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

import asyncpg

from app.ai.extractor import Extractor
from app.db import facts as facts_db
from app.db.profiles import get_user_weight_unit
from app.schemas.check_ins import (
    BodyweightEntryOut,
    CheckInFacts,
    NutritionEntryOut,
    SleepEntryOut,
    WorkoutSetOut,
)
from app.schemas.extraction import ExtractedFacts

# The international avoirdupois pound, exactly. Not an approximation: 135 lb is exactly
# 61.23496995 kg, and we round that to 61.235 — three decimals round-trips back to 135.0 lb
# and is finer than any bathroom or gym scale. Storing full precision would imply a
# certainty the measurement never had; storing fewer would lose the round trip.
_LB_TO_KG = Decimal("0.45359237")
_KG_PLACES = Decimal("0.001")

# 'done' is the outcome whenever extraction RAN and stored everything it found — including
# when it found nothing at all ("what's the weather") and when the text also contained
# prose it correctly ignored ("...and my boss is annoying"). Both are the model succeeding.
_STATUS_DONE = "done"
# 'partial' means exactly one thing: a fact WAS found but had to be thrown away, because
# its exercise isn't in the seeded catalog. Keeping this meaning narrow is what lets the UI
# say "one item didn't read" and be believed — if ordinary prose made a check-in partial,
# nearly every real check-in would be partial and the warning would be noise.
_STATUS_PARTIAL = "partial"
# 'failed' means extraction itself broke: the vendor was down, or its output failed
# validation. The raw text is always intact regardless — it is the source of truth and
# facts are merely derived from it.
_STATUS_FAILED = "failed"


def to_kg(weight: Decimal, weight_unit: str | None) -> Decimal:
    """Convert a weight in the user's unit to canonical kg, rounded to 3 decimals.

    `weight_unit` is whatever the profile says; anything that isn't 'kg' is treated as lb,
    matching the column's own default and CHECK (`weight_unit in ('lb','kg')`). Pure, so
    it's the piece worth testing hardest.
    """
    if weight_unit == "kg":
        return weight
    return (weight * _LB_TO_KG).quantize(_KG_PLACES, rounding=ROUND_HALF_UP)


async def extract_and_store(
    pool: asyncpg.Pool,
    user_id: UUID,
    check_in_id: UUID,
    raw_text: str,
    extractor: Extractor,
) -> tuple[str, CheckInFacts]:
    """Extract facts from `raw_text` and persist them onto `check_in_id`. Returns the
    `extraction_status` to stamp and exactly what was stored.

    Returning the facts (rather than making the caller re-SELECT them) keeps POST at zero
    extra reads: we just wrote these rows and know which ones were dropped.

    Raises whatever the extractor raises (vendor error, validation error) — the caller
    turns that into 'failed' and keeps the text. Nothing is written when it raises, so a
    failed re-run leaves the previous facts alone rather than clearing them.
    """
    extracted: ExtractedFacts = await extractor.extract(raw_text)

    # Read the unit only after extraction succeeded — no point querying for a call that
    # may be about to blow up.
    weight_unit = await get_user_weight_unit(pool, user_id)

    requested_sets = [
        (
            s.exercise_name,
            s.set_number,
            s.reps,
            # None stays None: a bodyweight move has no load, and 0 would drag every
            # strength average toward zero.
            None if s.weight is None else to_kg(s.weight, weight_unit),
        )
        for s in extracted.sets
    ]
    nutrition = [
        (n.description, n.calories, n.protein_g, n.carbs_g, n.fat_g, n.meal)
        for n in extracted.nutrition
    ]
    sleep = [(s.hours, s.quality) for s in extracted.sleep]
    bodyweight = [to_kg(b.weight, weight_unit) for b in extracted.bodyweight]

    stored = await facts_db.replace_facts(
        pool,
        user_id,
        check_in_id,
        sets=requested_sets,
        nutrition=nutrition,
        sleep=sleep,
        bodyweight=bodyweight,
    )

    facts = CheckInFacts(
        sets=[
            WorkoutSetOut(
                id=row_id, exercise_name=name, set_number=number, reps=reps, weight_kg=weight_kg
            )
            for row_id, name, number, reps, weight_kg in stored.sets
        ],
        nutrition=[
            NutritionEntryOut(
                id=row_id,
                description=description,
                calories=calories,
                protein_g=protein_g,
                carbs_g=carbs_g,
                fat_g=fat_g,
                meal=meal,
            )
            for row_id, description, calories, protein_g, carbs_g, fat_g, meal in stored.nutrition
        ],
        sleep=[
            SleepEntryOut(id=row_id, hours=hours, quality=quality)
            for row_id, hours, quality in stored.sleep
        ],
        bodyweight=[
            BodyweightEntryOut(id=row_id, weight_kg=kg) for row_id, kg in stored.bodyweight
        ],
    )

    # Anything that went in but didn't come back was dropped — the ONE thing 'partial'
    # means. Everything else, including finding nothing at all, is 'done'.
    #
    # Checked across ALL FOUR tables, not just sets. Only sets can be dropped by an
    # unknown exercise name today, so the other three can only come up short if the rule-4
    # parent-ownership guard blocked the insert — unreachable right now, because POST mints
    # its own check_in_id and always owns it. It's checked anyway because `extract_and_store`
    # is the durable choke point every future path to a fact row goes through (that is
    # precisely why row 14 is tested here rather than at an endpoint). The security guard is
    # durable; this status must be too, or the day a re-run endpoint lands, a caller aiming
    # at someone else's check-in gets a cheerful 'done' with nothing written.
    dropped_any = (
        len(stored.sets) != len(requested_sets)
        or len(stored.nutrition) != len(nutrition)
        or len(stored.sleep) != len(sleep)
        or len(stored.bodyweight) != len(bodyweight)
    )
    return (_STATUS_PARTIAL if dropped_any else _STATUS_DONE), facts


def failed_status() -> str:
    """The status for an extraction that broke. A function, not a bare import of a private
    name, so the meaning above travels with it."""
    return _STATUS_FAILED
