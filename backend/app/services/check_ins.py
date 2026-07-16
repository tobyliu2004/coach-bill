"""Check-in business logic: resolve the caller's local day, run extraction, map db rows to
API shapes. Routes call this, never db/. Services return None/False for a missing row; the
route decides that means 404.
"""

import logging
from uuid import UUID

import asyncpg

from app.ai.extractor import Extractor
from app.db import check_ins as check_ins_db
from app.db import facts as facts_db
from app.db.profiles import get_user_timezone
from app.schemas.check_ins import (
    BodyweightEntryOut,
    CheckInCreate,
    CheckInFacts,
    CheckInOut,
    NutritionEntryOut,
    SleepEntryOut,
    WorkoutSetOut,
)
from app.services.extraction import extract_and_store, failed_status
from app.time import local_today

logger = logging.getLogger(__name__)


async def create_check_in(
    pool: asyncpg.Pool, user_id: UUID, body: CheckInCreate, extractor: Extractor
) -> CheckInOut:
    """Store a check-in under the caller's local today, then extract facts from it.

    ORDER IS THE POINT: the text is committed BEFORE the model is called. The check-in row
    is the source of truth and facts are derived from it, so a dead vendor must cost the
    user their facts — never their words, and never their 201. Hence the broad `except`
    below: there is no failure of extraction that justifies losing text the user typed.
    """
    tz = await get_user_timezone(pool, user_id)
    row = await check_ins_db.insert_check_in(pool, user_id, body.text, local_today(tz))
    check_in_id: UUID = row["id"]

    facts = CheckInFacts()
    try:
        status, facts = await extract_and_store(pool, user_id, check_in_id, body.text, extractor)
    except Exception:
        # Deliberately broad. A vendor outage, a timeout, malformed output that failed
        # validation, a bug in our own mapping — from the user's side these are one thing
        # ("the robot didn't read it"), and the response is the same: keep the text, say so
        # honestly, let them re-run later. `logger.exception` keeps the real cause visible
        # to us in Render's logs; swallowing it silently is what makes this class of bug
        # unfindable.
        logger.exception("extraction failed for check_in_id=%s", check_in_id)
        status = failed_status()

    await check_ins_db.set_extraction_status(pool, user_id, check_in_id, status)
    # The INSERT returned the row as 'pending'; the decided status is the truth now.
    return CheckInOut(**{**dict(row), "extraction_status": status}, facts=facts)


async def list_check_ins(pool: asyncpg.Pool, user_id: UUID) -> list[CheckInOut]:
    """The caller's check-ins for their local today, newest first, facts bundled in."""
    tz = await get_user_timezone(pool, user_id)
    rows = await check_ins_db.list_check_ins_for_date(pool, user_id, local_today(tz))
    if not rows:
        return []

    # One batched fact read for the whole list, not one per check-in.
    by_table = await facts_db.list_facts_for_check_ins(pool, user_id, [r["id"] for r in rows])
    return [CheckInOut(**dict(row), facts=_facts_for(by_table, row["id"])) for row in rows]


async def delete_check_in(pool: asyncpg.Pool, user_id: UUID, check_in_id: UUID) -> bool:
    """Delete the caller's check-in; False if no such row (route turns that into 404).

    The fact tables cascade on `check_in_id`, so the derived rows go with it.
    """
    return await check_ins_db.delete_check_in(pool, user_id, check_in_id)


def _facts_for(by_table: dict[str, list[asyncpg.Record]], check_in_id: UUID) -> CheckInFacts:
    """Pick one check-in's facts out of the batched read."""

    def rows(table: str) -> list[asyncpg.Record]:
        return [r for r in by_table[table] if r["check_in_id"] == check_in_id]

    return CheckInFacts(
        sets=[
            WorkoutSetOut(
                exercise_name=r["exercise_name"],
                set_number=r["set_number"],
                reps=r["reps"],
                weight_kg=r["weight_kg"],
            )
            for r in rows("workout_sets")
        ],
        nutrition=[
            NutritionEntryOut(
                description=r["description"],
                calories=r["calories"],
                protein_g=r["protein_g"],
                carbs_g=r["carbs_g"],
                fat_g=r["fat_g"],
                meal=r["meal"],
            )
            for r in rows("nutrition_entries")
        ],
        sleep=[
            SleepEntryOut(hours=r["hours"], quality=r["quality"]) for r in rows("sleep_entries")
        ],
        bodyweight=[
            BodyweightEntryOut(weight_kg=r["weight_kg"]) for r in rows("bodyweight_entries")
        ],
    )
