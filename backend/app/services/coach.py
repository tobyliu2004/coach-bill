"""Coach Bill's business logic: decide, assemble, generate, store.

Routes call this, never db/. The route turns `None` into a 404 and `CoachUnavailable` into
a 503; it makes no other decisions.

THE ORDER OF OPERATIONS IS THE DESIGN. Reading `reply_to_check_in` top to bottom:

  1. Is this check-in the caller's?   -> no: 404, and we have spent nothing (AC rows 5/6)
  2. Has Bill already answered it?    -> yes: return that reply, zero model calls (row 2)
  3. What kind of message is this?    -> the gate; a failure here is a 503 (row 13)
  4. crisis / off_topic               -> a fixed constant, no Sonnet at all (rows 10/15/16)
  5. coach                            -> assemble context, generate, validate (rows 21-27)
  6. Store it, proving the parent     -> guard blocks: 404, no orphan (row 7)

Steps 1 and 2 both run before any model call, and that is not an optimisation. Step 1 is
what stops a stranger billing us by guessing ids, and step 2 is what stops a double-click
costing ~1.7¢ and storing a second reply.

FAILS CLOSED. Unlike `POST /check-ins`, which swallows extraction failures because losing
derived facts must never cost a user their words, nothing here is stored on a partial
success. A gate failure means we don't know whether this is a crisis; a coach failure means
we have no reply worth keeping. Both raise, the route answers 503, and the check-in itself
is already safe on disk — so "try again" is a real option rather than a hope. Storing a
broken reply would be permanently wrong, because step 2 would hand it back forever.
"""

import logging
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

import asyncpg

from app.ai.coach import CRISIS_REPLY, OFF_TOPIC_REPLY, Coach
from app.ai.gate import IntentGate
from app.db import check_ins as check_ins_db
from app.db import coach as coach_db
from app.db.profiles import get_profile
from app.schemas.check_ins import CheckInOut
from app.schemas.coach import CoachReplyOut
from app.schemas.trends import TrendsOut
from app.services.check_ins import list_check_ins
from app.services.trends import get_trends

logger = logging.getLogger(__name__)

# How much history Bill sees. Truncation is by COUNT OF DAYS, not by token estimate: a
# token budget would make the context a function of how much the user happened to write,
# so the same account could get a different window on different days and no test could pin
# it. Fourteen days is two training weeks — enough for a trend, short enough that Bill is
# talking about now (AC row 24).
CONTEXT_DAYS = 14

# How many of Bill's own previous replies ride along, so he doesn't open with the same
# sentence every morning. THREE IS A FLOOR, NOT A CONVERSATION: the issue is explicit that
# Bill responds to check-ins and is not a general chat interface, and a growing transcript
# is how this turns into one by accident (AC row 27).
RECENT_REPLIES = 3


class CoachUnavailable(Exception):
    """Either model failed, or produced something we refuse to store.

    Carries no user-facing text on purpose — the route decides what a 503 says. What must
    NOT happen is this becoming a stored reply: see the module docstring.
    """


class ReplyResult(NamedTuple):
    """A reply, and whether this request is what created it.

    `created` exists so the ROUTE can answer 201 vs 200 without re-reading the database or
    guessing. Get-or-create is one operation to the service and two status codes to HTTP,
    and this is the seam between those two facts (AC rows 1/2).
    """

    reply: CoachReplyOut
    created: bool


def build_context(
    *,
    goal: str | None,
    weight_unit: str,
    trends: TrendsOut,
    check_ins: list[CheckInOut],
    recent_replies: list[str],
) -> str:
    """Everything Bill knows about one person, as one string. PURE.

    No pool, no clock, no I/O — the parameters are the whole world. That is what makes the
    interesting rows assertable at all: "does A's context contain any of B's data" (AC row
    22) and "does a 14-day window stop at day 14" (row 24) are questions about a value, and
    a function that could reach for `datetime.now()` or a connection would make them
    questions about an environment instead. Same doctrine as `app/time.py` on the backend
    and `lib/` on the frontend: decisions live in pure functions, effects live at the edges.

    Byte-identical for identical inputs (row 21). Nothing here formats a date it wasn't
    given, so today's date can only appear if the caller passed it in.

    Written for a model to read, not a human: labelled sections, one fact per line, no
    prose. The coach prompt tells Bill to reference actual numbers, and this is where those
    numbers have to be unambiguous enough for him to do it without inventing any.
    """
    sections: list[str] = []

    sections.append(f"GOAL\n{goal}" if goal else "GOAL\nNot set.")
    sections.append(f"WEIGHT UNIT\nShow weights in {weight_unit}.")

    # Sparse by construction: a day with nothing logged is simply absent, never a row of
    # nulls. A rendered empty day would claim the user logged nothing, when the truth is
    # they didn't log — the same distinction `groupByDay` keeps on the frontend.
    if check_ins:
        lines = [
            f"- {c.entry_date.isoformat()}: {c.raw_text.strip()}"
            for c in check_ins
            if c.raw_text.strip()
        ]
        sections.append("RECENT CHECK-INS (newest first)\n" + "\n".join(lines))
    else:
        # AC row 25, the first-run case. Said explicitly rather than left as an empty
        # heading, so Bill answers today's check-in instead of narrating history he can't
        # see — a reply citing trends that don't exist is the feature failing on day one.
        sections.append("RECENT CHECK-INS\nNone yet — this is their first check-in.")

    sections.append(_trends_section(trends))

    if recent_replies:
        # Bill's own words, so he can avoid repeating them. Explicitly labelled as his, or
        # the model reads them as more of the user's history.
        numbered = "\n".join(f"- {reply.strip()}" for reply in recent_replies)
        sections.append("YOUR LAST FEW REPLIES (do not repeat these)\n" + numbered)

    return "\n\n".join(sections)


def _trends_section(trends: TrendsOut) -> str:
    """The computed rollup, rendered. Numbers are carried through untouched.

    Every value here was computed by Postgres (db/trends.py). Nothing is recomputed,
    rounded, or unit-converted on the way past — a second opinion about what the user did
    is exactly the kind of quiet divergence that makes a coach untrustworthy.
    """
    header = f"TRENDS ({trends.start_date.isoformat()} to {trends.end_date.isoformat()})"
    lines: list[str] = []

    for point in trends.volume:
        # `volume_kg` is None, never 0, on a day of purely unweighted work — a day of
        # pushups did not have zero tonnage, it had none that can be measured. Saying
        # "0 kg" here would hand Bill a number that means the opposite of the truth.
        if point.volume_kg is not None:
            lines.append(f"- {point.date.isoformat()}: {_num(point.volume_kg)} kg total volume")
        if point.bodyweight_sets:
            lines.append(
                f"- {point.date.isoformat()}: {point.bodyweight_sets} bodyweight sets, "
                f"{point.bodyweight_reps} reps (no external load to measure)"
            )

    for summary in trends.exercises:
        heaviest = f", heaviest {_num(summary.heaviest_kg)} kg" if summary.heaviest_kg else ""
        lines.append(f"- {summary.name}: {summary.sets} sets, {summary.reps} reps{heaviest}")

    for night in trends.sleep:
        quality = f", quality {night.quality}/5" if night.quality is not None else ""
        lines.append(f"- {night.date.isoformat()}: slept {_num(night.hours)}h{quality}")

    for weight in trends.bodyweight:
        lines.append(f"- {weight.date.isoformat()}: bodyweight {_num(weight.weight_kg)} kg")

    for day in trends.nutrition:
        lines.append(
            f"- {day.date.isoformat()}: {_num(day.calories)} kcal, "
            f"{_num(day.protein_g)}g protein, {_num(day.carbs_g)}g carbs, {_num(day.fat_g)}g fat"
        )

    if not lines:
        return f"{header}\nNothing logged in this window."
    return header + "\n" + "\n".join(lines)


def _num(value: Decimal) -> str:
    """A Decimal as the shortest exact string. `normalize` drops trailing zeros so 135.00
    reads as 135, and the quantize guards `normalize`'s scientific notation on integers
    (Decimal('100').normalize() is '1E+2', which is not a weight anyone recognises)."""
    normalized = value.normalize()
    if normalized == normalized.to_integral_value():
        return str(normalized.quantize(Decimal(1)))
    return str(normalized)


async def reply_to_check_in(
    pool: asyncpg.Pool,
    user_id: UUID,
    check_in_id: UUID,
    gate: IntentGate,
    coach: Coach,
) -> ReplyResult | None:
    """Get or create Bill's reply to one check-in. None means 404 (see the module docstring)."""
    # 1. OWNERSHIP FIRST, BEFORE ANY SPEND. Someone else's id matches nothing and is
    #    indistinguishable from a nonexistent one — a 404, never a 403 (AC rows 5/6).
    check_in = await check_ins_db.get_check_in(pool, user_id, check_in_id)
    if check_in is None:
        return None

    # 2. ALREADY ANSWERED? Return it. Zero model calls (AC row 2).
    existing = await coach_db.get_reply_for_check_in(pool, user_id, check_in_id)
    if existing is not None:
        return ReplyResult(_reply_out(existing), created=False)

    raw_text: str = check_in["raw_text"]

    # 3. THE GATE. Any failure is fatal to the request — we do not know what this message
    #    is, and guessing is what the gate exists to prevent (AC row 13).
    try:
        intent = await gate.classify(raw_text)
    except Exception as exc:
        logger.exception("intent gate failed for check_in_id=%s", check_in_id)
        raise CoachUnavailable("the intent gate is unavailable") from exc

    # 4/5. ROUTE. The two fixed replies never touch Sonnet — that is both the cost control
    #      and, for `crisis`, the safety control (AC rows 10/15/16).
    if intent.label == "crisis":
        content = CRISIS_REPLY
    elif intent.label == "off_topic":
        content = OFF_TOPIC_REPLY
    elif intent.label == "coach":
        content = await _coach_reply(pool, user_id, raw_text, coach)
    else:
        # Untrusted output that didn't validate is a failure, NEVER a default (AC row 14).
        # Defaulting to `coach` would send an unclassified crisis to Sonnet; defaulting to
        # `off_topic` would silently drop one. Neither is an acceptable way to be wrong.
        logger.error("intent gate returned an unknown label %r", intent.label)
        raise CoachUnavailable(f"unknown intent label: {intent.label!r}")

    # 6. STORE, proving the parent inside the write. The check-in can have been deleted
    #    since step 1 — the guard, not that read, is what makes this safe (AC row 7).
    row = await coach_db.insert_reply(pool, user_id, check_in_id, content)
    if row is None:
        return None
    return ReplyResult(_reply_out(row), created=True)


async def _coach_reply(pool: asyncpg.Pool, user_id: UUID, raw_text: str, coach: Coach) -> str:
    """Assemble the context, generate, and refuse anything not worth storing."""
    context = await _context_for(pool, user_id)
    try:
        content = await coach.reply(context, raw_text)
    except Exception as exc:
        logger.exception("the coach model failed for user_id=%s", user_id)
        raise CoachUnavailable("the coach is unavailable") from exc

    # AC row 35. An empty or whitespace-only reply is a failed generation wearing the shape
    # of a successful one. Storing it would put a blank message on the user's screen
    # permanently, because step 2 hands back whatever is stored.
    stripped = content.strip()
    if not stripped:
        raise CoachUnavailable("the coach returned an empty reply")
    return stripped


async def _context_for(pool: asyncpg.Pool, user_id: UUID) -> str:
    """Read this user's world, then hand it to the pure builder.

    Every read here is owner-scoped by the function it calls; none of them can see another
    user's rows, which is what makes AC row 22 a property of the system rather than of this
    function's care. The effects live here; the decisions live in `build_context`.
    """
    profile = await get_profile(pool, user_id)
    trends = await get_trends(pool, user_id, CONTEXT_DAYS)
    check_ins = await list_check_ins(pool, user_id, CONTEXT_DAYS)

    # Bill's recent replies come from the check-ins we ALREADY read rather than a fourth
    # query: `list_check_ins` bundles them (AC row 30) and returns newest-day-first, so the
    # first few replies in that list are the most recent ones he wrote (AC row 27).
    recent = [c.reply.content for c in check_ins if c.reply is not None][:RECENT_REPLIES]

    return build_context(
        goal=profile["goal"] if profile is not None else None,
        # Same fallback as the column's own default, so a profile that predates the column
        # renders in the unit the rest of the app assumes rather than crashing the reply.
        weight_unit=(profile["weight_unit"] if profile is not None else None) or "lb",
        trends=trends,
        check_ins=check_ins,
        recent_replies=recent,
    )


def _reply_out(row: asyncpg.Record) -> CoachReplyOut:
    """Map a stored `coach_messages` row to the API shape."""
    return CoachReplyOut(id=row["id"], content=row["content"], created_at=row["created_at"])


__all__ = [
    "CONTEXT_DAYS",
    "RECENT_REPLIES",
    "CoachUnavailable",
    "ReplyResult",
    "build_context",
    "reply_to_check_in",
]
