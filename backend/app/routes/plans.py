"""`POST /plans` and `GET /plans/current` (issue #51). HTTP only — no logic lives here."""

from fastapi import APIRouter, HTTPException, status

from app.ai.planner import PlannerDep
from app.auth import UserIdDep
from app.deps import PoolDep
from app.schemas.plans import PlanCreate, PlanOut
from app.services import plans as plans_service

router = APIRouter(prefix="/plans", tags=["plans"])


@router.post("", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
async def create_plan(
    body: PlanCreate, pool: PoolDep, user_id: UserIdDep, planner: PlannerDep
) -> PlanOut:
    """Generate and store a program for the caller.

    ROW 8 IS ENFORCED BEFORE THIS FUNCTION RUNS. `PlanCreate.weeks` is `ge=1, le=8`, so
    `{"weeks": 53}` is a 422 from FastAPI's own validation — no DB round trip, no model call,
    nothing spent. Declaring the bound on the schema rather than checking it in the body is
    what makes "spends nothing" true rather than merely likely, and `extra="forbid"` means a
    payload that also names `user_id` or `calories_target` is rejected outright instead of
    silently ignored.

    `user_id` comes from `UserIdDep` and nowhere else (`backend.md` rule 1).
    """
    try:
        return await plans_service.create_plan(pool, user_id, body.weeks, planner)
    except plans_service.PlannerUnavailable as exc:
        # 503, not 500: nothing was stored and the request is worth retrying. The same
        # shape as the coach reply's failure, and the deliberate opposite of extraction's
        # swallow — see `PlannerUnavailable`'s docstring for why this path fails closed.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bill could not write your plan just now. Try again in a moment.",
        ) from exc


@router.get("/current", response_model=PlanOut)
async def get_current_plan(pool: PoolDep, user_id: UserIdDep) -> PlanOut:
    """The caller's active plan.

    The SERVICE returns None for "no active plan"; the ROUTE is what decides that means 404
    (`backend.md`). There is no resource id in this path at all — the plan is found from the
    verified caller — so there is no id to be untrusted, and a stranger cannot name someone
    else's plan to probe for it. Row 11 asserts B gets a 404 while A has an active plan.
    """
    plan = await plans_service.get_current_plan(pool, user_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active plan.",
        )
    return plan
