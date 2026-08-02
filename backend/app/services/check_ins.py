"""Check-in business logic: resolve the caller's local day, run extraction, map db rows to
API shapes. Routes call this, never db/. Services return None/False for a missing row; the
route decides that means 404.
"""

import logging
from datetime import timedelta
from uuid import UUID

import asyncpg

from app.ai.extractor import Extractor
from app.db import check_ins as check_ins_db
from app.db import coach as coach_db
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
from app.schemas.coach import CoachReplyOut
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

    try:
        status = await extract_and_store(pool, user_id, check_in_id, body.text, extractor)
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
    # Read the facts back through the SAME path GET uses, rather than echoing what we just
    # wrote. Consistency by construction: the read resolves each set's exercise through the
    # catalog, so a check-in logged as "curls" comes back as `barbell curl` here exactly as
    # it will on the next list. Echoing the model's raw name instead made POST and GET
    # disagree about the same row — invisible today only because the screen refetched.
    facts = _facts_for(
        _group_by_check_in(await facts_db.list_facts_for_check_ins(pool, user_id, [check_in_id])),
        check_in_id,
    )
    # The INSERT returned the row as 'pending'; the decided status is the truth now.
    return CheckInOut(**{**dict(row), "extraction_status": status}, facts=facts)


async def list_check_ins(pool: asyncpg.Pool, user_id: UUID, days: int = 1) -> list[CheckInOut]:
    """The caller's check-ins over the last `days` local days, newest first, facts bundled in.

    The window is INCLUSIVE of today and of its first day: `days=7` is today plus the six
    before it, not today plus seven. That reading is a judgment call (issue #20, AC row 2) —
    a user asking for "7 days" means a week of their life, and the alternative silently shows
    an eight-day week.

    Both ends come from `local_today(tz)`, so the window is anchored to the USER's calendar
    day, never the server's. At 00:30 UTC an evening check-in in Los Angeles is still today's
    (AC row 3); computing this from the server clock is precisely how it would vanish.

    `days` defaults to 1 so the whole window collapses to `today..today` — byte-identical in
    effect to the single-date query this replaced, which is what keeps the existing `/app`
    screen (and every existing caller) unchanged.
    """
    tz = await get_user_timezone(pool, user_id)
    end = local_today(tz)
    # days - 1: the window already contains `end` itself, so a 1-day window subtracts nothing.
    start = end - timedelta(days=days - 1)
    rows = await check_ins_db.list_check_ins_in_range(pool, user_id, start, end)
    if not rows:
        return []

    check_in_ids = [r["id"] for r in rows]
    # One batched fact read for the whole list, not one per check-in...
    by_table = await facts_db.list_facts_for_check_ins(pool, user_id, check_in_ids)
    # ...and one batched reply read alongside it, for the same reason (AC row 30). Two
    # queries total regardless of how many days the window covers; a per-check-in reply
    # lookup would be an N+1 that grows with the user's history.
    replies = await _replies_by_check_in(pool, user_id, check_in_ids)
    # ...and one pass to index it. Filtering the full result list once per check-in would
    # reintroduce, in memory, the same N+1 shape the batched read exists to avoid.
    grouped = _group_by_check_in(by_table)
    return [
        CheckInOut(
            **dict(row),
            facts=_facts_for(grouped, row["id"]),
            reply=replies.get(row["id"]),
        )
        for row in rows
    ]


async def _replies_by_check_in(
    pool: asyncpg.Pool, user_id: UUID, check_in_ids: list[UUID]
) -> dict[UUID, CoachReplyOut]:
    """Index the batched reply read by check-in id.

    `setdefault`, not assignment, so the OLDEST reply wins on the (schema-permitted, app-
    prevented) chance that a check-in has more than one. That is not arbitrary: it matches
    `db/coach.py::get_reply_for_check_in`, which the reply endpoint uses for its
    get-or-create. If the two disagreed, `POST /check-ins/{id}/reply` and `GET /check-ins`
    would hand back different replies for the same check-in and the screen would appear to
    change its mind on refresh.
    """
    indexed: dict[UUID, CoachReplyOut] = {}
    for row in await coach_db.list_replies_for_check_ins(pool, user_id, check_in_ids):
        indexed.setdefault(
            row["check_in_id"],
            CoachReplyOut(id=row["id"], content=row["content"], created_at=row["created_at"]),
        )
    return indexed


async def delete_check_in(pool: asyncpg.Pool, user_id: UUID, check_in_id: UUID) -> bool:
    """Delete the caller's check-in; False if no such row (route turns that into 404).

    The fact tables cascade on `check_in_id`, so the derived rows go with it.
    """
    return await check_ins_db.delete_check_in(pool, user_id, check_in_id)


def _group_by_check_in(
    by_table: dict[str, list[asyncpg.Record]],
) -> dict[str, dict[UUID, list[asyncpg.Record]]]:
    """Index the batched fact read by check-in id, in one pass per table."""
    grouped: dict[str, dict[UUID, list[asyncpg.Record]]] = {}
    for table, records in by_table.items():
        table_index: dict[UUID, list[asyncpg.Record]] = {}
        for record in records:
            table_index.setdefault(record["check_in_id"], []).append(record)
        grouped[table] = table_index
    return grouped


def _facts_for(
    grouped: dict[str, dict[UUID, list[asyncpg.Record]]], check_in_id: UUID
) -> CheckInFacts:
    """Pick one check-in's facts out of the indexed read."""

    def rows(table: str) -> list[asyncpg.Record]:
        return grouped[table].get(check_in_id, [])

    return CheckInFacts(
        sets=[
            WorkoutSetOut(
                id=r["id"],
                exercise_name=r["exercise_name"],
                set_number=r["set_number"],
                reps=r["reps"],
                weight_kg=r["weight_kg"],
            )
            for r in rows("workout_sets")
        ],
        nutrition=[
            NutritionEntryOut(
                id=r["id"],
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
            SleepEntryOut(id=r["id"], hours=r["hours"], quality=r["quality"])
            for r in rows("sleep_entries")
        ],
        bodyweight=[
            BodyweightEntryOut(id=r["id"], weight_kg=r["weight_kg"])
            for r in rows("bodyweight_entries")
        ],
    )
