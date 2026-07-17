"""Oracle suite for issue #19, section E — model quality (rows 21-23). LIVE API, gated.

Part of commit #1 on `feat/19-ai-extraction`, written BEFORE any implementation exists.
These encode section E of the 23-row correctness table Toby approved on 2026-07-16.

**These are the only thing checking the prompt.** Every other test in this ticket injects a
fake `Extractor`, which means they prove our plumbing and ASSUME the AI works. Rows 21-23
are the assumption. They call `claude-haiku-4-5` for real, so they cost money and cannot
run in CI (no key there, by Toby's "no pay-per-token API billing in CI" rule — the model is
on prepaid credits with auto-recharge OFF).

Gating, mirroring test_rls_identity.py's RLS_DATABASE_URL style — one env var switches the
whole tier on, and a missing companion fails LOUDLY rather than pretending to pass:
  - LIVE_MODEL_TESTS  — set to any non-empty value to opt in. Unset (CI, and every normal
                        local run) -> the whole file skips.
  - ANTHROPIC_API_KEY — the key. If LIVE_MODEL_TESTS is set without it, the tests FAIL with
                        an explanation instead of silently skipping, so "I ran the model
                        tests" can never quietly mean "I ran nothing".
Run them by hand before shipping a prompt change:
    LIVE_MODEL_TESTS=1 uv run --env-file .env pytest tests/test_extraction_live_model.py

These assert on the extractor's OUTPUT SHAPE (ExtractedFacts, in the user's own units — the
lb->kg conversion is our service's job and is covered deterministically by rows 1/2/6), not
on the wording of the response. They are quality gates on comprehension, not on phrasing.
"""

import os
from decimal import Decimal
from typing import Any

import pytest

requires_live_model = pytest.mark.skipif(
    not os.getenv("LIVE_MODEL_TESTS"),
    reason="LIVE_MODEL_TESTS not set; live Haiku model-quality suite skipped (costs money)",
)


def _extractor() -> Any:
    """The real HaikuExtractor, or fail loudly if the key is missing."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.fail(
            "LIVE_MODEL_TESTS is set but ANTHROPIC_API_KEY is not; these tests must call "
            "the real model or not run at all — a silent skip here would mean the prompt "
            "is unverified while looking verified."
        )
    from app.ai.extractor import HaikuExtractor

    return HaikuExtractor()


# AC row 21: "bench 135 4x8" -> 4 sets of 8 reps @ 135 — NOT 4 reps x 8 sets.
# The issue's own named question, and Toby's ruling: lifting convention is sets x reps
# (PLAN.md writes "3x8 bench"). The two readings are perfectly symmetric numerically, so
# asserting BOTH the count and the reps is the only way to tell them apart.
@requires_live_model
async def test_row21_4x8_means_four_sets_of_eight_reps() -> None:
    facts = await _extractor().extract("bench 135 4x8")

    assert len(facts.sets) == 4  # four SETS...
    assert [s.reps for s in facts.sets] == [8, 8, 8, 8]  # ...of eight REPS (not 8 sets of 4)
    assert [s.weight for s in facts.sets] == [Decimal("135")] * 4  # user's own unit, unconverted
    assert sorted(s.set_number for s in facts.sets) == [1, 2, 3, 4]
    assert all("bench" in s.exercise_name.lower() for s in facts.sets)


# AC row 22: "slept 6h, ate 4 eggs, knee felt tweaky" -> sleep 6h; ONE nutrition entry;
# NO fact row for the knee. Qualitative notes are issue #22's job — forcing "knee felt
# tweaky" into a numeric table is corruption, and inventing a set for it is the exact
# failure this row exists to catch.
@requires_live_model
async def test_row22_qualitative_note_produces_no_fact_row() -> None:
    facts = await _extractor().extract("slept 6h, ate 4 eggs, knee felt tweaky")

    assert len(facts.sleep) == 1
    assert facts.sleep[0].hours == Decimal("6")

    assert len(facts.nutrition) == 1  # ONE entry, not one per macro
    assert "egg" in facts.nutrition[0].description.lower()

    assert facts.sets == []  # the knee is not a workout set
    assert facts.bodyweight == []  # nor a bodyweight measurement


# AC row 23: the canonical PLAN.md transcript "3x8 bench at 135, slept 6h, ate 4 eggs" ->
# all three fact types, exact numbers. The example the whole product is described by should
# work end to end.
@requires_live_model
async def test_row23_plan_md_transcript_yields_all_three_fact_types() -> None:
    facts = await _extractor().extract("3x8 bench at 135, slept 6h, ate 4 eggs")

    # 3 sets x 8 reps @ 135 (same sets x reps convention as row 21)
    assert len(facts.sets) == 3
    assert [s.reps for s in facts.sets] == [8, 8, 8]
    assert [s.weight for s in facts.sets] == [Decimal("135")] * 3
    assert all("bench" in s.exercise_name.lower() for s in facts.sets)

    assert len(facts.sleep) == 1
    assert facts.sleep[0].hours == Decimal("6")

    assert len(facts.nutrition) == 1
    assert "egg" in facts.nutrition[0].description.lower()

    assert facts.bodyweight == []  # no bodyweight was mentioned; none may be invented
