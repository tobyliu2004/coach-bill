"""Shared route dependencies (used by more than one router)."""

from typing import Annotated

import asyncpg
from fastapi import Depends, Request


def get_pool(request: Request) -> asyncpg.Pool:
    """Dependency: hand the route the pool created at startup (see the app lifespan)."""
    return request.app.state.pool


PoolDep = Annotated[asyncpg.Pool, Depends(get_pool)]
