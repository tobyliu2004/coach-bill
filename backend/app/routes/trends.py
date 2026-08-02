"""The caller's computed trends. Routes call services; they never touch db/ directly.

`user_id` comes only from `UserIdDep` (the JWT-verified id). This endpoint takes no resource
id at all — the only thing the client names is the size of the window.
"""

from typing import Annotated

from fastapi import APIRouter, Query

from app.auth import UserIdDep
from app.deps import PoolDep
from app.schemas.trends import TrendsOut
from app.services.trends import get_trends

router = APIRouter()


@router.get("/trends", response_model=TrendsOut)
async def read_trends(
    user_id: UserIdDep,
    pool: PoolDep,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> TrendsOut:
    """The caller's trends over the last `days` of their local days.

    `days` defaults to 30, matching /history's window, so the dashboard and the log describe
    the same span rather than two spans that merely look similar.

    The bounds are the same deliberate ones GET /check-ins carries, and they matter more
    here: `ge=1` rejects 0 and negatives with a 422 rather than quietly coercing them (a
    negative window would invert the BETWEEN and render an empty dashboard with no error at
    all), and `le=365` is the ONLY bound on response size — there is no pagination in v1 and
    this endpoint returns five series at once. The cap is inclusive: 365 is accepted, 366 is
    a 422 (AC rows 5/6).

    Declaring the bounds HERE rather than in the service is what makes AC row 5's other half
    true — FastAPI rejects the request before the handler body runs, so a malformed `days`
    never costs a database round trip.

    An empty window is a 200 carrying the window and five empty series, never a 404.
    """
    return await get_trends(pool, user_id, days)
