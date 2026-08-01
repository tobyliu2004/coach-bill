"""The caller's check-ins. Routes call services; they never touch db/ directly.

`user_id` comes only from `UserIdDep` (the JWT-verified id); the `check_in_id` in the
DELETE path is an untrusted client claim, proven-or-denied inside the delete statement.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.ai.extractor import ExtractorDep
from app.auth import UserIdDep
from app.deps import PoolDep
from app.schemas.check_ins import CheckInCreate, CheckInOut
from app.services.check_ins import create_check_in, delete_check_in, list_check_ins

router = APIRouter()

_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Check-in not found")


@router.post("/check-ins", response_model=CheckInOut, status_code=status.HTTP_201_CREATED)
async def post_check_in(
    user_id: UserIdDep, pool: PoolDep, body: CheckInCreate, extractor: ExtractorDep
) -> CheckInOut:
    """Log a new text check-in under the caller's local today, and extract its facts.

    Extraction runs inside this request, so the response already carries what was read.
    It cannot fail the request: a broken extraction still returns 201 with the raw text
    intact and `extraction_status` telling the truth about what happened.
    """
    return await create_check_in(pool, user_id, body, extractor)


@router.get("/check-ins", response_model=list[CheckInOut])
async def get_check_ins(
    user_id: UserIdDep,
    pool: PoolDep,
    days: Annotated[int, Query(ge=1, le=365)] = 1,
) -> list[CheckInOut]:
    """The caller's check-ins over the last `days` of their local days; `[]` when there are none.

    `days` defaults to 1 — today only, exactly what this endpoint returned before the window
    existed, so the daily screen's call is unchanged.

    The bounds are the validation, and they are deliberate rather than decorative. `ge=1`
    rejects 0 and negatives with a 422 instead of quietly coercing them: a zero window is
    meaningless and a negative one would invert the BETWEEN into a silently empty screen,
    which is a client bug we would be hiding (AC rows 5/6). `le=365` is the ONLY bound on
    response size — there is no pagination in v1 — and it is inclusive: 365 is accepted, 366
    is a 422 (AC rows 7/8).

    An empty window is a 200 with `[]`, never a 404. "You logged nothing that month" is an
    answer, not a failure, and a 404 would make the screen render an error over it.
    """
    return await list_check_ins(pool, user_id, days)


@router.delete("/check-ins/{check_in_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_check_in(user_id: UserIdDep, pool: PoolDep, check_in_id: UUID) -> Response:
    """Delete one of the caller's check-ins. Someone else's id is a 404, not a 403 — a 403
    would confirm the row exists."""
    deleted = await delete_check_in(pool, user_id, check_in_id)
    if not deleted:
        raise _NOT_FOUND
    return Response(status_code=status.HTTP_204_NO_CONTENT)
