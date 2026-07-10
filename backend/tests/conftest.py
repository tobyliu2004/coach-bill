"""Shared test fixtures.

The app builds `Settings` (which requires DATABASE_URL) at import time, so we set a
dummy value *before* importing the app. Unit tests never actually connect — they run
without the lifespan and override the pool dependency — so the dummy is never dialed.
We record whether a *real* DATABASE_URL was provided so the integration test can gate on it.
"""

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

if os.environ.get("DATABASE_URL"):
    os.environ["HAS_REAL_DB"] = "1"
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """An HTTP client bound to the ASGI app *without* running the lifespan (no real pool)."""
    from app.main import app  # imported lazily, after the env is configured above

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
    app.dependency_overrides.clear()
