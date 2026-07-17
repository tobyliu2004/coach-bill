"""The caller's check-ins. Routes call services; they never touch db/ directly.

`user_id` comes only from `UserIdDep` (the JWT-verified id); the `check_in_id` in the
DELETE path is an untrusted client claim, proven-or-denied inside the delete statement.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

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
async def get_check_ins(user_id: UserIdDep, pool: PoolDep) -> list[CheckInOut]:
    """The caller's check-ins for today (their local day); an empty list when there are none."""
    return await list_check_ins(pool, user_id)


@router.delete("/check-ins/{check_in_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_check_in(user_id: UserIdDep, pool: PoolDep, check_in_id: UUID) -> Response:
    """Delete one of the caller's check-ins. Someone else's id is a 404, not a 403 — a 403
    would confirm the row exists."""
    deleted = await delete_check_in(pool, user_id, check_in_id)
    if not deleted:
        raise _NOT_FOUND
    return Response(status_code=status.HTTP_204_NO_CONTENT)
