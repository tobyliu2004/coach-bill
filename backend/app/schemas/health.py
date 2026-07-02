"""API response shapes for health checks."""

from typing import Literal

from pydantic import BaseModel


class HealthStatus(BaseModel):
    """Result of a database health check."""

    ok: bool
    db: Literal["up", "down"]
    latency_ms: float
