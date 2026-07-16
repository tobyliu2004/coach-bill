"""Shared test fixtures.

The app builds `Settings` (which requires DATABASE_URL) at import time, so we set a
dummy value *before* importing the app. Unit tests never actually connect — they run
without the lifespan and override the pool dependency — so the dummy is never dialed.
We record whether a *real* DATABASE_URL was provided so the integration test can gate on it.
"""

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient

if os.environ.get("DATABASE_URL"):
    os.environ["HAS_REAL_DB"] = "1"
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
# Same trick for the extraction key: Settings requires it at import time, but the unit
# suite overrides `get_extractor` with a fake, so this dummy is never sent anywhere. The
# gated live-model tests (tests/test_extraction_live_model.py) require a REAL key and fail
# loudly rather than skip if LIVE_MODEL_TESTS is set without one.
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")


@pytest.fixture(autouse=True)
def no_live_model() -> Iterator[None]:
    """Default EVERY unit test to a do-nothing Extractor — the network is never reachable.

    Without this, any test that POSTs a check-in without overriding `get_extractor` builds
    a real `HaikuExtractor` and dials Anthropic: slow, flaky, and billable from CI. This is
    the same move as the dummy DATABASE_URL above — the unit suite fakes its boundaries.

    It is a DEFAULT, not a lock: tests that care about extraction (test_extraction.py) set
    their own `get_extractor` override afterwards and win, because a later assignment to
    `dependency_overrides` replaces this one. Returning empty facts is the least
    presumptuous stand-in: it exercises the real success path and writes nothing.
    """
    from app.ai.extractor import get_extractor
    from app.main import app
    from app.schemas.extraction import ExtractedFacts

    class _NullExtractor:
        async def extract(self, text: str) -> ExtractedFacts:
            return ExtractedFacts()

    app.dependency_overrides[get_extractor] = lambda: _NullExtractor()
    yield
    app.dependency_overrides.clear()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """An HTTP client bound to the ASGI app *without* running the lifespan (no real pool)."""
    from app.main import app  # imported lazily, after the env is configured above

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
    app.dependency_overrides.clear()
