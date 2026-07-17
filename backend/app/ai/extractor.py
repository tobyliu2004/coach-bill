"""The swappable boundary between Coach Bill and the model.

Everything above this file talks to the `Extractor` Protocol, never to Anthropic. That
buys three things:
  * CI never calls the live API — tests inject a fake through `ExtractorDep` (the same
    shape as `PoolDep`), so the merge gate costs nothing and can't flake on a vendor.
  * Swapping models (or vendors) is one class, not a grep.
  * `get_nutrition` is a seam *inside* the boundary: today it returns Haiku's estimate;
    when USDA FoodData Central lands (a known, accepted debt — see PLAN.md "Deferred"),
    it changes here and no caller moves.
"""

from functools import lru_cache
from typing import Annotated, Protocol

from anthropic import AsyncAnthropic
from fastapi import Depends

from app.config import get_settings
from app.schemas.extraction import ExtractedFacts, ExtractedNutrition

_MODEL = "claude-haiku-4-5"

# A check-in is a couple of sentences and the reply is a small JSON object; 2048 leaves
# generous room for a long multi-exercise log without inviting a runaway.
_MAX_TOKENS = 2048

# The whole call sits inside POST /check-ins, so this timeout is a user-facing latency
# budget, not just a safety net. A slow vendor must fail fast and leave the check-in
# 'failed' (AC row 9) rather than hold the request open. The SDK retries 429/5xx twice by
# default, so worst-case wall clock is roughly 3x this.
_TIMEOUT_SECONDS = 30.0

# No prompt caching, deliberately: Haiku 4.5's minimum cacheable prefix is 4096 tokens and
# this system prompt is ~900. A `cache_control` marker here would silently never cache —
# all cost, no benefit, and a misleading line of code. (See the model's row in the caching
# minimums table.) No thinking either: this is extraction, not reasoning.
_SYSTEM_PROMPT = """\
You extract structured fitness facts from a person's daily check-in text.

Return ONLY facts the text actually states. Never infer, never round, never invent. If the \
text contains no fitness facts at all, return empty lists — that is a correct answer, not a \
failure.

WEIGHTS
- Report the number as written. Do NOT convert units: the caller knows the user's unit and \
converts. "bench 135" -> weight: 135.
- Bodyweight movements with no external load (pushups, pullups, air squats, planks) have \
weight: null. Never use 0.

SETS AND REPS
- "4x8", "4 x 8", "4 sets of 8" all mean 4 SETS of 8 REPS. The first number is sets.
- Emit ONE object per set. "bench 135 4x8" is four objects, set_number 1, 2, 3 and 4, each \
with reps: 8 and weight: 135.
- set_number always starts at 1 for each exercise.

EXERCISE NAMES
- Use the movement's PLAIN, CANONICAL English name, lowercase and singular-ish: "bench \
press", "squat", "pull-up", "romanian deadlift". Not a description, not a sentence, not a \
nickname.
- Resolve the user's shorthand to the standard name: "bench" -> "bench press", "OHP" -> \
"overhead press", "RDL" -> "romanian deadlift", "pushups"/"push ups"/"press ups" -> \
"pushups".
- The name is matched against a fixed catalog of known movements. A name that isn't in it is \
DISCARDED along with its sets, so the canonical name is what gets the user's work logged. \
Never invent a name to be descriptive ("john's shoulder finisher"), never attach anything \
personal, and never put anything but the movement itself in this field.

SLEEP
- hours is a number 0-24. quality is 1-5 ONLY if the person actually rates it; otherwise \
null. Never guess a quality.

NUTRITION
- One object per food item. description is what they ate ("4 eggs").
- calories, protein_g, carbs_g and fat_g are all REQUIRED — give your best estimate for the \
stated quantity. These are estimates today and that is expected.
- meal is breakfast/lunch/dinner/snack only if stated; otherwise null.

BODYWEIGHT
- Only when they state their OWN body weight ("weighed in at 180"), never a lifted weight.

NOT FACTS
- Feelings, pain, mood, plans, and everything else qualitative are NOT facts. "knee felt \
tweaky" and "my boss is annoying" produce NO rows of any kind. They are handled elsewhere."""


class Extractor(Protocol):
    """What the rest of the app is allowed to know about the model."""

    async def extract(self, text: str) -> ExtractedFacts:
        """Pull structured facts out of one check-in's raw text.

        Raises on vendor failure or unparseable/invalid output — the caller catches, marks
        the check-in 'failed', and keeps the raw text (AC rows 9, 10).
        """
        ...


def get_nutrition(item: ExtractedNutrition) -> ExtractedNutrition:
    """The macros for one food item — the USDA seam.

    Today: pass through Haiku's estimate. AI-estimated macros are a known, accepted debt
    (the issue and PLAN.md both say so). Tomorrow: look `item.description` up in USDA
    FoodData Central and return real numbers. Callers never change, because they already
    ask *this* function rather than reading the model's numbers directly.

    It stays a plain function (not a method) because it is about food, not about the model —
    the USDA implementation won't call Anthropic at all.
    """
    return item


class HaikuExtractor:
    """The real `Extractor` — `claude-haiku-4-5` with structured output.

    `messages.parse` + `output_format` makes the SDK enforce our Pydantic schema and hand
    back a validated `ExtractedFacts`, so there is no hand-rolled JSON parsing to get wrong.
    Constraints the API's JSON-schema dialect doesn't support (our `ge`/`le` bounds) are
    stripped from the wire schema and validated client-side by Pydantic — so junk like
    reps = -1 still raises `ValidationError` here rather than reaching Postgres (AC row 10).
    """

    def __init__(self, client: AsyncAnthropic | None = None) -> None:
        # Defaults to the shared process-wide client, so `HaikuExtractor()` just works
        # (that's how the live-model tests construct it). Still injectable for anything
        # that needs a differently-configured client.
        self._client = client if client is not None else _client()

    async def extract(self, text: str) -> ExtractedFacts:
        response = await self._client.messages.parse(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            output_format=ExtractedFacts,
            messages=[{"role": "user", "content": text}],
        )
        facts = response.parsed_output
        if facts is None:
            # Structured output failed to produce a valid object (e.g. the model hit
            # max_tokens mid-JSON). Untrusted output that didn't validate is a failure,
            # not an empty result — an empty result would silently mean "nothing to
            # extract" (AC row 11) and mark a broken extraction 'done'.
            raise ValueError(f"Haiku returned no parsable facts (stop: {response.stop_reason})")

        # Route every food item through the USDA seam on the way out, so callers get
        # whatever `get_nutrition` decides is authoritative — today, Haiku's estimate.
        return facts.model_copy(update={"nutrition": [get_nutrition(n) for n in facts.nutrition]})


@lru_cache
def _client() -> AsyncAnthropic:
    """One client for the process — it holds a connection pool; building one per request
    would throw that away. Cached like `get_settings`."""
    return AsyncAnthropic(
        api_key=get_settings().anthropic_api_key,
        timeout=_TIMEOUT_SECONDS,
    )


def get_extractor() -> Extractor:
    """Dependency: hand the route the live extractor. Tests override this (see
    `app.dependency_overrides`) so CI never spends a token."""
    return HaikuExtractor()


ExtractorDep = Annotated[Extractor, Depends(get_extractor)]
