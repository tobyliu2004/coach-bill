"""API response shapes for health checks."""

from pydantic import BaseModel


class HealthStatus(BaseModel):
    """Result of a database health check."""

    ok: bool
    db: str  # "up" when reachable, "down" otherwise
    latency_ms: float
