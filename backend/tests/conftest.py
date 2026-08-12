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
    """Default EVERY unit test to do-nothing AI boundaries — the network is never reachable.

    Without this, any test that POSTs a check-in without overriding `get_extractor` builds
    a real `HaikuExtractor` and dials Anthropic: slow, flaky, and billable from CI. This is
    the same move as the dummy DATABASE_URL above — the unit suite fakes its boundaries.

    It is a DEFAULT, not a lock: tests that care about a boundary (test_extraction.py,
    test_coach.py) set their own override afterwards and win, because a later assignment to
    `dependency_overrides` replaces this one. Each stand-in is the least presumptuous one
    available: it exercises the real success path and asserts nothing.

    ⚠️ THE THREE ARE NOT EQUALLY LOAD-BEARING, and it is worth being honest about which.
    `get_extractor` is reachable from POST /check-ins, which many tests across many files
    call — without the fake, those really would dial the vendor. `get_gate`/`get_coach` are
    reachable from exactly ONE endpoint (POST /check-ins/{id}/reply), and every test that
    calls it overrides them itself, so today these two are belt-and-braces rather than the
    thing standing between CI and a bill. They are here because the cost of the next
    endpoint forgetting is a real charge against prepaid credits with auto-recharge OFF, and
    the cost of the fake is four lines.
    """
    from app.ai.coach import get_coach
    from app.ai.extractor import get_extractor
    from app.ai.gate import Intent, get_gate
    from app.main import app
    from app.schemas.extraction import ExtractedFacts

    class _NullExtractor:
        async def extract(self, text: str) -> ExtractedFacts:
            return ExtractedFacts()

    class _NullGate:
        """Labels everything `coach` — the path that exercises the most plumbing.

        Deliberately not `crisis`: that short-circuits before the coach, so a test that
        forgot to override would silently never reach half the code it thinks it is
        covering. (`coach` and `crisis` are the only two labels after issue #48.)
        """

        async def classify(self, text: str) -> Intent:
            return Intent(label="coach")

    class _NullCoach:
        async def reply(self, context: str, text: str) -> str:
            return "stand-in reply"

    app.dependency_overrides[get_extractor] = lambda: _NullExtractor()
    app.dependency_overrides[get_gate] = lambda: _NullGate()
    app.dependency_overrides[get_coach] = lambda: _NullCoach()
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
