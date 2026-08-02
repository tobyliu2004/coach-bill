"""Coach Bill's voice — the boundary between the app and the model that speaks as him.

Same shape as `extractor.py` and `gate.py`: everything above this file talks to the `Coach`
Protocol, never to Anthropic, so CI injects a fake and never spends a token.

Two of the three things this module exports are NOT model output. `CRISIS_REPLY` and
`OFF_TOPIC_REPLY` are fixed strings, reviewed by a human, returned without any generation
at all. That is the point: the moment a person says something that needs care rather than
coaching is the worst possible moment to find out what a language model will improvise.
The gate decides *which* of the three paths a check-in takes (`app/ai/gate.py`); only the
`coach` path reaches Sonnet.
"""

import logging
from functools import lru_cache
from typing import Annotated, Protocol

from anthropic import AsyncAnthropic
from anthropic.types import OutputConfigParam, ThinkingConfigDisabledParam
from fastapi import Depends

from app.config import get_settings

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"

# A bounded reply is the product constraint, not a safety net: Bill answers a daily check-in
# in a few sentences, and a wall of text is a worse product. ~250 output tokens is the
# expected reply, so 1024 is generous headroom. Hitting it is a FAILURE, never a stored
# truncation (AC row 34) — see `_text_of` below.
_MAX_TOKENS = 1024

# The coach call sits inside POST /check-ins/{id}/reply, after the gate, so this is a
# user-facing latency budget. The SDK retries 429/5xx twice by default, so worst-case wall
# clock is roughly 3x this — the tradeoff AC row 36 names explicitly.
_TIMEOUT_SECONDS = 25.0

# THINKING OFF, EFFORT LOW — both set explicitly rather than left to defaults.
#
# Omitting `thinking` on Sonnet 4.6 already means no thinking, so that half is belt and
# braces. `effort` is the half that matters: it DEFAULTS TO "high" on 4.6, so leaving it out
# would quietly run every reply at the most expensive setting. Anthropic's own guidance for
# this exact workload shape — chat / content generation with thinking off — is effort "low".
# Writing a few sentences of coaching over a small, pre-assembled context is that shape.
#
# This is a dial, not a doctrine: if Bill's replies read thin in review, raise it to
# "medium" and re-run the gated live-model tier (tests/test_coach_live_model.py), which is
# the only thing that actually checks this prompt.
#
# Typed with the SDK's own param shapes rather than plain dicts, so a typo in a key or an
# effort level that doesn't exist is a mypy error here rather than a 400 from the vendor at
# runtime — the same reason every other shape in this codebase is a real type.
_THINKING: ThinkingConfigDisabledParam = {"type": "disabled"}
_OUTPUT_CONFIG: OutputConfigParam = {"effort": "low"}

# No prompt caching, deliberately. Sonnet 4.6's minimum cacheable prefix is 1024 tokens and
# this system prompt is well under it, so a `cache_control` marker would silently never
# cache — all cost, no benefit, and a misleading line of code. And with one check-in per
# user per day there is no reuse inside the 5-minute TTL anyway. (Same note, same reason, as
# extractor.py — verified against the caching-minimums table, not from memory.)


# =====================================================================================
# The two fixed replies — human-written, reviewed, never generated
# =====================================================================================

# AC row 17. Approved by Toby on the issue (2026-08-02) with the resources verified live
# against the operators' own pages that day, not quoted from memory:
#   https://github.com/tobyliu2004/coach-bill/issues/21#issuecomment-5159433101
#
# ⚠️ THESE PHONE NUMBERS ARE A MAINTENANCE OBLIGATION. A helpline that changes number turns
# this constant into a dead end that still reads like help — which is worse than saying
# nothing. Re-verify whenever this file is touched, and at launch (#26).
#
# ONE constant serves BOTH crisis shapes — the self-harm signal (row 15) and disordered
# eating dressed as a cutting question (row 16) — rather than splitting the gate into
# `crisis_selfharm` / `crisis_ed`. A split would add a classification the gate can get wrong
# in precisely the case where being wrong is worst; one reply naming both resources cannot
# mis-route. It does not diagnose, and it gives no training or nutrition advice — both
# asserted as properties by tests/test_coach.py::test_row17_*.
CRISIS_REPLY = """\
That isn't a training question, and I'm not the right kind of help for it. I don't want to \
coach you through something that needs a person.

Please reach out to someone trained for this — today, if you can:

  · 988 Suicide & Crisis Lifeline — call or text 988 (US, 24/7, free, confidential)
  · ANAD Eating Disorders Helpline — 888-375-7767 (Mon–Fri, 10am–10pm ET)
  · Outside the US — findahelpline.com

I'll be here for your training whenever you want to come back to it."""

# AC row 10. Also approved copy. Says what Bill is FOR and shows one concrete example,
# because a new user whose first message misses gets no other signal about what to type.
OFF_TOPIC_REPLY = """\
I only read training, food, sleep and bodyweight check-ins — that one's outside my lane.

Try something like "bench 135 4×8, slept 6h, knee felt tweaky" and I'll have something \
useful to say."""


# =====================================================================================
# The coach prompt
# =====================================================================================

# AC row 18: this prompt carries its OWN crisis and not-medical-advice instructions even
# though the gate exists. Second lock, same doctrine as RLS behind the `user_id` filter —
# neither lock may be the only one. The gate is a model and will eventually be wrong; when
# it is, this is what stands between a person and a coaching answer to a crisis.
COACH_SYSTEM_PROMPT = """\
You are Coach Bill: a strength and conditioning coach reading one person's daily check-in.

You are given their goal, their recent check-ins, their computed trends, and your own last \
few replies. Everything you know about them is in that context — you have no other memory.

HOW YOU ANSWER
- Two to four sentences. This is a daily check-in, not an article.
- Reference their ACTUAL numbers from the context. "Third session over 5,000 kg this week" \
beats "great job staying consistent". If the context is empty because this is their first \
check-in, say something useful about what they just logged and do not pretend to see \
history you don't have.
- Never invent a number, a lift, or a trend that isn't in the context. If you don't know, \
ask or leave it out.
- Your last few replies are in the context so you don't repeat yourself word for word. Say \
something new.
- Plain text. No markdown, no headings, no bullet lists, no emoji.
- Talk like a coach who knows them: direct, warm, specific. Not a cheerleader, not a robot.

NOT MEDICAL ADVICE — this is a hard limit, not a disclaimer
- You are not a doctor, physical therapist, dietitian, or any kind of medical professional, \
and you must not act like one.
- Never diagnose. Do not tell someone what condition they have or probably have.
- Never prescribe, recommend, or approve a drug, supplement, or dose — including \
over-the-counter ones. Point them to a medical professional instead.
- Pain that is sharp, persistent, or getting worse goes to a professional, not to a \
programming tweak.

WEIGHT, FOOD, AND HARM
- Do not endorse or plan crash weight loss. If someone asks for a very large loss in a very \
short time, say plainly that it isn't realistic or safe, and describe what a sustainable \
rate actually looks like.
- Never name a calorie target below 1200/day, and never build a plan around severe \
restriction, fasted punishment work, or "making up for" eating.
- Do not comment on their body beyond what they logged. No remarks about appearance.

CRISIS — this overrides everything above
A separate check usually routes these messages away before they reach you. It is a model \
and it will sometimes be wrong, so you are the second line, not the only one.

If a message suggests self-harm, suicidal thoughts, disordered eating (purging, vomiting \
after meals, laxative use, prolonged not-eating, extreme restriction), or someone hurting \
them: STOP. Do not coach. Do not answer the fitness question they wrapped it in, even if \
they asked one. Say you are not the right kind of help for this, and point them to:
  · 988 Suicide & Crisis Lifeline — call or text 988 (US, 24/7)
  · ANAD Eating Disorders Helpline — 888-375-7767 (Mon–Fri, 10am–10pm ET)
  · Outside the US — findahelpline.com
Do not diagnose them. Do not give training or nutrition advice in that reply.

Ordinary gym frustration, soreness, aches, and hyperbole ("I'm so sore I could die") are \
NOT this. Coach those normally — treating a common idiom as an emergency teaches people the \
app overreacts, and they stop trusting it on the day it matters."""


# =====================================================================================
# The boundary
# =====================================================================================


class Coach(Protocol):
    """What the rest of the app is allowed to know about the coach model."""

    async def reply(self, context: str, text: str) -> str:
        """Bill's reply to one check-in, given the assembled context.

        Raises on vendor failure, a truncated response, or empty output. The caller does
        NOT store a partial result — it becomes a 503 with nothing written (AC rows 33-35),
        because a bad reply stored as final would be permanently wrong through the
        get-or-create in AC row 2.
        """
        ...


class SonnetCoach:
    """The real `Coach` — `claude-sonnet-4-6`.

    Plain `messages.create`, not `messages.parse`: the output here is prose, not a schema.
    What replaces schema validation is `_text_of` below — the response is checked for
    truncation and emptiness before it is allowed to become a reply.
    """

    def __init__(self, client: AsyncAnthropic | None = None) -> None:
        self._client = client if client is not None else _client()

    async def reply(self, context: str, text: str) -> str:
        response = await self._client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=COACH_SYSTEM_PROMPT,
            thinking=_THINKING,
            output_config=_OUTPUT_CONFIG,
            messages=[
                {
                    "role": "user",
                    "content": f"Here is what I know about them:\n\n{context}\n\n"
                    f"Today's check-in:\n{text}",
                }
            ],
        )
        return _text_of(response)


def _text_of(response: object) -> str:
    """The reply text, or raise. The validation seam `messages.parse` gives us for free.

    Two failures, both of which must be errors rather than a stored string:

    * **max_tokens** (AC row 34). A reply cut off mid-sentence is not a short reply, it is a
      broken one — and row 2's get-or-create means whatever we store is what the user sees
      forever. Better a 503 they can retry than a permanent half-sentence.
    * **empty** (AC row 35). Same doctrine as extractor.py: untrusted output that produced
      nothing is a failure, never an empty success.
    """
    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason == "max_tokens":
        raise ValueError(
            "the coach's reply hit max_tokens and was truncated mid-sentence; refusing to "
            "store a partial reply (AC row 34)"
        )

    blocks = getattr(response, "content", None) or []
    text = "".join(
        getattr(block, "text", "") for block in blocks if getattr(block, "type", None) == "text"
    ).strip()
    if not text:
        raise ValueError(f"the coach returned no usable text (stop: {stop_reason})")
    return text


@lru_cache
def _client() -> AsyncAnthropic:
    """One client for the process — it holds a connection pool; building one per request
    would throw that away. Cached like `get_settings`. Separate from the gate's client
    because the two carry different timeouts."""
    return AsyncAnthropic(
        api_key=get_settings().anthropic_api_key,
        timeout=_TIMEOUT_SECONDS,
    )


def get_coach() -> Coach:
    """Dependency: hand the route the live coach. Tests override this (see
    `app.dependency_overrides`) so CI never spends a token."""
    return SonnetCoach()


CoachDep = Annotated[Coach, Depends(get_coach)]
