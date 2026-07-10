"""Health-check HTTP routes. Routes call services; they never touch the db directly."""

import logging

from fastapi import APIRouter, Response, status

from app.deps import PoolDep
from app.schemas.health import HealthStatus
from app.services.health import check_db

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health/db", response_model=HealthStatus)
async def health_db(pool: PoolDep, response: Response) -> HealthStatus:
    """Report whether the database is reachable, with round-trip latency.

    Returns 200 when up; 503 when the database can't be reached, so uptime monitors and
    deploy platforms can detect an unhealthy instance.
    """
    try:
        return await check_db(pool)
    except Exception:  # any failure means the dependency is unhealthy
        # Log the real reason — a 503 with no cause is undebuggable in production.
        logger.exception("database health check failed")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthStatus(ok=False, db="down", latency_ms=0.0)
