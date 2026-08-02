"""Coach Bill's reply endpoint. Routes call services; they never touch db/ directly.

`user_id` comes only from `UserIdDep` (the JWT-verified id); the `check_in_id` in the path
is an untrusted client claim, proven-or-denied inside the statements the service runs.

WHY THIS IS ITS OWN ENDPOINT rather than inline in `POST /check-ins`. Logging a check-in is
fast and must never fail; generating a reply is slow (3-6s) and is allowed to. Folding them
together would mean a dead vendor could cost a user their words — the exact failure #18's
write path is built to prevent — or that every check-in waits on Sonnet before it is saved.
Separating them also makes the reply retryable on its own, which is what turns every 503
below into "try again" rather than "you lost it".
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from app.ai.coach import CoachDep
from app.ai.gate import GateDep
from app.auth import UserIdDep
from app.deps import PoolDep
from app.schemas.coach import CoachReplyOut
from app.services.coach import CoachUnavailable, reply_to_check_in, retract_off_topic_reply

router = APIRouter()

_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Check-in not found")

# 503, not 500: the request was fine and the state is unchanged, so retrying is the correct
# next move and the client should be told so. A 500 would read as "we are broken", and a 200
# with an apology in the body would be a stored reply — the one thing that must not happen.
_UNAVAILABLE = HTTPException(
    status.HTTP_503_SERVICE_UNAVAILABLE,
    "Bill couldn’t answer just now — your check-in is saved. Try again in a moment.",
)


@router.post(
    "/check-ins/{check_in_id}/reply",
    response_model=CoachReplyOut,
    status_code=status.HTTP_201_CREATED,
)
async def post_reply(
    user_id: UserIdDep,
    pool: PoolDep,
    check_in_id: UUID,
    gate: GateDep,
    coach: CoachDep,
    response: Response,
) -> CoachReplyOut:
    """Get or create Bill's reply to one of the caller's check-ins.

    Takes no body. The text being replied to is the check-in already on disk, so there is
    nothing for a client to send — and nothing that could disagree with `check_ins.raw_text`.

    **201 the first time, 200 every time after** (AC rows 1/2). Idempotent get-or-create:
    a double-click, a component remount, or a retry after a dropped connection returns the
    reply that already exists instead of spending both models again and storing a second
    one. The status code is the only difference the client sees, and it is honest about
    which request did the work.

    Someone else's `check_in_id` is a 404, not a 403 — a 403 would confirm the row exists.
    A malformed one is a 422 from path validation, before any of this runs.
    """
    try:
        result = await reply_to_check_in(pool, user_id, check_in_id, gate, coach)
    except CoachUnavailable as exc:
        # The service already logged the cause with a stack trace; this is the boundary
        # where a failed generation becomes an HTTP answer, and nothing has been stored.
        raise _UNAVAILABLE from exc

    if result is None:
        raise _NOT_FOUND
    if not result.created:
        # FastAPI applied the 201 from the decorator; this is the documented way to vary it
        # per request. Mutating the injected Response is what lets one handler be honest
        # about get-vs-create without splitting the endpoint in two.
        response.status_code = status.HTTP_200_OK
    return result.reply


@router.delete("/check-ins/{check_in_id}/reply", status_code=status.HTTP_204_NO_CONTENT)
async def retract_reply(user_id: UserIdDep, pool: PoolDep, check_in_id: UUID) -> Response:
    """Drop a stored **off-topic** reply so the caller can ask Bill again.

    The gate is a model and will eventually mislabel a real training check-in as off-topic;
    without this, `POST`'s get-or-create hands that constant back forever and the only
    escape is deleting the check-in and retyping it. (PR #47 review.)

    **Only an off-topic reply can be retracted, and that is a safety property.** A
    `CRISIS_REPLY` is refused — if it were not, someone in genuine crisis could ask again
    and again until the gate handed them coaching instead of the hotlines, and the app would
    be re-rolling away from its own safety response. A real coach reply is refused too, for
    a duller reason: "give me a different answer" is a request to spend money again, and
    that belongs behind the per-user caps in #26.

    Everything that is not a retractable off-topic reply is a **404** — no reply, someone
    else's check-in, a nonexistent one, a crisis reply, a real reply. One shape for all of
    them on purpose: a distinct code for "exists but you may not touch it" would confirm
    both that the row exists and what kind it is, which is the enumeration oracle backend
    rule 5 exists to prevent.
    """
    if not await retract_off_topic_reply(pool, user_id, check_in_id):
        raise _NOT_FOUND
    return Response(status_code=status.HTTP_204_NO_CONTENT)
