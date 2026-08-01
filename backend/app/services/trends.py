"""Trends business logic: resolve the caller's window, then map aggregate rows to API shapes.

The arithmetic itself is Postgres's (see db/trends.py). What lives here is the one decision
SQL cannot make — *which days are we talking about* — and the mapping out to Pydantic.
"""

from datetime import timedelta
from uuid import UUID

import asyncpg

from app.db import trends as trends_db
from app.db.profiles import get_user_timezone
from app.schemas.trends import (
    BodyweightPoint,
    ExerciseSummary,
    NutritionPoint,
    SleepPoint,
    TrendsOut,
    VolumePoint,
)
from app.time import local_today


async def get_trends(pool: asyncpg.Pool, user_id: UUID, days: int = 30) -> TrendsOut:
    """The caller's computed trends over the last `days` of their local days.

    The window math is IDENTICAL to `list_check_ins` on purpose — inclusive of both ends, so
    `days=7` is today plus the six before it, not an eight-day week. /trends and /history are
    two readings of the same span, and a different off-by-one on one of them would be worse
    than the same one on both: the dashboard and the log would disagree about the month the
    user is looking at.

    Both ends come from `local_today(tz)`, never `datetime.now` — the USER's calendar day,
    not the server's. At 00:30 UTC an evening session in Los Angeles still belongs to the
    LA date (AC row 3), and computing this from the server clock is precisely how a day's
    training gets aggregated into a day the user has not lived yet. Reusing the shared helper
    is also what makes the window pinnable: the tests freeze `app.time`, so a second source
    of "today" would silently escape the freeze — which is the #18 bug's exact shape.

    An empty window is a 200 carrying the window and five empty series, never a 404. "You
    logged nothing in July" is an answer, and the screen needs `start_date`/`end_date` to
    say *which* July it means.
    """
    tz = await get_user_timezone(pool, user_id)
    end = local_today(tz)
    # days - 1: the window already contains `end` itself, so a 1-day window subtracts nothing.
    start = end - timedelta(days=days - 1)
    series = await trends_db.fetch_trends(pool, user_id, start, end)

    # Straight mapping, no arithmetic. Every number below was computed by Postgres and is
    # carried through untouched — a total recomputed here would be a second, silently
    # divergent opinion about what the user did.
    return TrendsOut(
        start_date=start,
        end_date=end,
        volume=[
            VolumePoint(
                date=r["entry_date"],
                volume_kg=r["volume_kg"],
                bodyweight_sets=r["bodyweight_sets"],
                bodyweight_reps=r["bodyweight_reps"],
            )
            for r in series["volume"]
        ],
        exercises=[
            ExerciseSummary(
                name=r["name"],
                sets=r["sets"],
                reps=r["reps"],
                volume_kg=r["volume_kg"],
                heaviest_kg=r["heaviest_kg"],
            )
            for r in series["exercises"]
        ],
        sleep=[
            SleepPoint(date=r["entry_date"], hours=r["hours"], quality=r["quality"])
            for r in series["sleep"]
        ],
        bodyweight=[
            BodyweightPoint(date=r["entry_date"], weight_kg=r["weight_kg"])
            for r in series["bodyweight"]
        ],
        nutrition=[
            NutritionPoint(
                date=r["entry_date"],
                calories=r["calories"],
                protein_g=r["protein_g"],
                carbs_g=r["carbs_g"],
                fat_g=r["fat_g"],
            )
            for r in series["nutrition"]
        ],
    )
