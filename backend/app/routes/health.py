"""Health-check HTTP routes. Routes call services; they never touch the db directly."""

from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends, Request, Response, status

from app.schemas.health import HealthStatus
from app.services.health import check_db

router = APIRouter()


def get_pool(request: Request) -> asyncpg.Pool:
    """Dependency: hand the route the pool created at startup (see the app lifespan)."""
    return request.app.state.pool


PoolDep = Annotated[asyncpg.Pool, Depends(get_pool)]


@router.get("/health/db", response_model=HealthStatus)
async def health_db(pool: PoolDep, response: Response) -> HealthStatus:
    """Report whether the database is reachable, with round-trip latency.

    Returns 200 when up; 503 when the database can't be reached, so uptime monitors and
    deploy platforms can detect an unhealthy instance.
    """
    try:
        return await check_db(pool)
    except Exception:  # any failure means the dependency is unhealthy
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthStatus(ok=False, db="down", latency_ms=0.0)
