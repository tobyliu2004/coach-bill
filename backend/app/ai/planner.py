"""The swappable boundary between the plan feature and the model (issue #51).

Same shape as `app/ai/extractor.py` and `app/ai/coach.py`, deliberately: everything above
this file talks to the `Planner` Protocol, never to Anthropic, so CI injects a fake through
`PlannerDep` and the merge gate costs nothing.

TWO CHOICES WORTH THE PARAGRAPH:

**`claude-sonnet-4-6`, not Haiku.** The extractor's job is parsing — turn "bench 135 4x8"
into rows. This one writes a mesocycle off a person's own numbers: it has to read a 185 lb
bench and choose working weights that build toward a goal, decide a split, and pace four
weeks of progression. That is coaching, and it runs on the coach's model. What it borrows
from the extractor is the *shape* — `messages.parse` with a Pydantic `output_format` — not
the model.

**The model writes ONE WEEK, not 28 days.** A 28-day structured output is ~4-5k tokens,
40-60 seconds, and the model pads it with filler. A weekly split is also how real programs
are written. `services/plans.materialize` expands the template into dated days as a PURE
function, which is what makes rows 6 and 7 testable with no model at all.
"""

from functools import lru_cache
from typing import Annotated, Protocol

from anthropic import AsyncAnthropic
from anthropic.types import OutputConfigParam, ThinkingConfigDisabledParam
from fastapi import Depends

from app.config import get_settings
from app.schemas.plans import DAYS_PER_WEEK, MAX_WEEKS, MIN_CALORIES_TARGET, PlanTemplate

_MODEL = "claude-sonnet-4-6"

# One week of training plus four macro numbers and a sentence. Seven days of items is the
# bulk of it; 4096 is generous headroom for a five-lift day without inviting a runaway.
# Hitting the cap means `parsed_output` is None, which is a failure, never a stored partial.
_MAX_TOKENS = 4096

# This call sits inside POST /plans and the user is watching a "Bill is writing your plan…"
# state, so the budget is longer than the coach's 25s — a program is worth waiting for — but
# it is still bounded. The SDK retries 429/5xx twice by default, so worst-case wall clock is
# roughly 3x this. A timeout is row 5: 503, nothing stored, retryable.
_TIMEOUT_SECONDS = 60.0

# THINKING OFF, EFFORT MEDIUM — both explicit, and the second one is not a copy-paste.
#
# Omitting `thinking` on Sonnet 4.6 already means no thinking, so that half is belt and
# braces. `effort` is the half that matters: it DEFAULTS TO "high" on 4.6, so leaving it out
# runs every plan at the most expensive setting. `coach.py` sets "low" because writing a few
# sentences over a pre-assembled context is a light workload. This one is not: it has to
# hold a week's structure together, keep the volume coherent across seven days, and derive
# loads from the user's actual numbers. "medium" is the deliberate middle.
#
# A dial, not a doctrine. If plans come back generic or the loads ignore the history, raise
# it and re-run the gated live tier (tests/test_plans_live_model.py) — that tier, and only
# that tier, can tell you whether a change to this file worked.
_THINKING: ThinkingConfigDisabledParam = {"type": "disabled"}
_OUTPUT_CONFIG: OutputConfigParam = {"effort": "medium"}

# No prompt caching, for the same measured reason as coach.py: Sonnet 4.6's minimum
# cacheable prefix is 1024 tokens, and one plan per user per month has no reuse inside the
# 5-minute TTL regardless. A `cache_control` marker would be all cost and no benefit.


# =====================================================================================
# The prompt
# =====================================================================================
#
# ⚠️ DESCRIBE THE APP AND THE CATEGORY. DO NOT ENUMERATE EXAMPLES.
#
# This is the rule #48 cost three separate bugs to learn, and this file is exactly where it
# would bite next. `GATE_SYSTEM_PROMPT` listed off-topic *examples* instead of describing
# what the app is, so the single most on-topic request in the product — "plan my next month
# of workouts" — was refused. Then the tests written to fix that made the same mistake one
# level up, and three CORRECT live replies went red on vocabulary.
#
# So: no list of acceptable splits, no menu of exercise names, no catalogue of goals. Say
# what a good program IS and let the model apply it. The one thing enumerated below is the
# hard floor on calories, and that is a numeric limit, not a phrasing.

PLANNER_SYSTEM_PROMPT = f"""\
You write training programs for people using Coach Bill, a fitness app where they log \
workouts, food, sleep and bodyweight in their own words each day.

You are given everything the app knows about one person: their goal if they set one, their \
recent check-ins, and computed trends from what they actually logged. You write ONE WEEK of \
training plus their daily nutrition targets. The app repeats that week for the length of \
the program and puts real dates on it, so write a week that is worth repeating.

THE WEEK
- Return exactly 7 days, in order, starting with the first day of the program.
- Every day has a `focus`: a short label for what that day is. A day with no training is a \
rest day and must say so. Never leave a focus empty.
- A rest day has no items. A training day has one item per SET — three sets of eight is \
three items with set_number 1, 2 and 3, each with reps 8.
- Choose how many days train and how they are split from what this person actually does. \
Someone logging three sessions a week should not be handed six.

LOADS — THIS IS THE PART THAT MAKES IT THEIRS
- Every working weight comes from a number this person has actually logged. If their bench \
is 185, their bench work is prescribed around 185 — not a round number you would have \
picked for anybody.
- If they have logged nothing for a movement, either leave `weight_kg` null and let the \
reps and the focus carry the instruction, or work from a movement they HAVE logged.
- `weight_kg` is null for anything with no external load. Never write 0 — 0 means they are \
lifting nothing, which is not what a bodyweight movement is.
- Weights are in kilograms. Convert if the history you are shown is in pounds.

MOVEMENT NAMES
- Use the plain, canonical English name of the movement, lowercase: "bench press", \
"back squat", "romanian deadlift", "pull-up". Not a description, not a sentence, not a \
nickname, and never anything personal to this user.
- Names are matched against a fixed catalog. One the catalog does not know is dropped from \
the plan, so the standard name is what gets the work programmed.

PROGRESSION
- `progression_note` is one line the person reads on their plan screen. Say what CHANGES \
from week to week — what moves, and by how much. A note that only restates week one is \
useless to them, because the app is about to repeat week one several times.

NUTRITION
- Give daily targets for calories, protein, carbs and fat, chosen for this person's goal \
and their logged intake.
- NEVER set a calorie target below {MIN_CALORIES_TARGET} per day, no matter what they are asking \
for or how fast they want to get there. This is a hard floor, not a guideline. If they want \
an aggressive cut, give them the most aggressive deficit that respects it and let the \
program do the rest.
- Targets are for one day and apply to every day of the week.

You are writing for one person, off their own numbers. A program that would suit anyone is \
the wrong answer."""


class Planner(Protocol):
    """What the rest of the app is allowed to know about the model."""

    async def plan(self, *, context: str, weeks: int) -> PlanTemplate:
        """One week of training plus diet targets, for the person `context` describes.

        `weeks` is passed so the progression note can pace itself over the real length of
        the program, NOT so the model writes that many days — it always writes seven.

        Raises on vendor failure or output that does not validate. The caller turns that
        into a 503 with nothing stored (row 5): this path FAILS CLOSED, unlike extraction,
        because half a plan is worse than no plan.
        """
        ...


class SonnetPlanner:
    """The real `Planner` — `claude-sonnet-4-6` with structured output.

    `messages.parse` + `output_format` makes the SDK enforce `PlanTemplate` and hand back a
    validated instance, so there is no hand-rolled JSON parsing to get wrong. Constraints
    the API's JSON-schema dialect does not support (our `ge` bounds) are stripped from the
    wire schema and validated client-side by Pydantic — which is precisely what makes row 2
    work: a model that returns `calories_target: 900` raises `ValidationError` HERE, out of
    this method, before `create_plan` opens a transaction.
    """

    def __init__(self, client: AsyncAnthropic | None = None) -> None:
        # Defaults to the shared process-wide client, so `SonnetPlanner()` just works (how
        # the live-model tests construct it). Still injectable.
        self._client = client if client is not None else _client()

    async def plan(self, *, context: str, weeks: int) -> PlanTemplate:
        response = await self._client.messages.parse(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=PLANNER_SYSTEM_PROMPT,
            thinking=_THINKING,
            output_config=_OUTPUT_CONFIG,
            output_format=PlanTemplate,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Here is what I know about them:\n\n{context}\n\n"
                        f"Write one week of training and their daily nutrition targets. "
                        f"The app will repeat this week for {weeks} "
                        f"week{'s' if weeks != 1 else ''} "
                        f"(up to {MAX_WEEKS}), one day per calendar day, "
                        f"{DAYS_PER_WEEK} days per week."
                    ),
                }
            ],
        )
        template = response.parsed_output
        if template is None:
            # Structured output produced nothing valid — most likely max_tokens mid-JSON.
            # Untrusted output that did not validate is a FAILURE, not an empty plan: an
            # empty plan would be stored and read as advice.
            raise ValueError(
                f"the planner returned no parsable template (stop: {response.stop_reason})"
            )
        return template


@lru_cache
def _client() -> AsyncAnthropic:
    """One client for the process — it holds a connection pool; building one per request
    would throw that away. Cached like `get_settings`."""
    return AsyncAnthropic(
        api_key=get_settings().anthropic_api_key,
        timeout=_TIMEOUT_SECONDS,
    )


def get_planner() -> Planner:
    """Dependency: hand the route the live planner. Tests override this (see
    `app.dependency_overrides`) so CI never spends a token."""
    return SonnetPlanner()


PlannerDep = Annotated[Planner, Depends(get_planner)]
