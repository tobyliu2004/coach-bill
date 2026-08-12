"""Coach Bill's voice — the boundary between the app and the model that speaks as him.

Same shape as `extractor.py` and `gate.py`: everything above this file talks to the `Coach`
Protocol, never to Anthropic, so CI injects a fake and never spends a token.

`CRISIS_REPLY` is NOT model output. It is a fixed string, reviewed by a human, returned
without any generation at all. That is the point: the moment a person says something that
needs care rather than coaching is the worst possible moment to find out what a language
model will improvise. The gate decides which of the two paths a check-in takes
(`app/ai/gate.py`); everything that is not a crisis reaches Sonnet.

**There used to be a second fixed reply, and deleting it is issue #48.** The off-topic reply
answered anything the gate judged not-about-fitness with "that one's outside my lane" — and
the gate judged *"plan my next month of workouts and diet"* to be one of those. Scope is not
a classification problem with a clean line; it is a judgement, so it belongs to Bill, in
prose, the way a real coach handles a question he can't take. `COACH_SYSTEM_PROMPT` below
therefore gained a section describing what this app actually is, so that "can you put my
plan in the dashboard" has an honest answer available instead of a guess.
"""

import logging
from functools import lru_cache
from typing import Annotated, Protocol

from anthropic import AsyncAnthropic
from anthropic.types import Message, OutputConfigParam, ThinkingConfigDisabledParam
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
# The one fixed reply — human-written, reviewed, never generated
# =====================================================================================

# AC row 17. Approved by Toby on the issue (2026-08-02) with the resources verified live
# against the operators' own pages that day, not quoted from memory:
#   https://github.com/tobyliu2004/coach-bill/issues/21#issuecomment-5159433101
#
# ⚠️ THESE PHONE NUMBERS ARE A MAINTENANCE OBLIGATION. A helpline that changes number turns
# this constant into a dead end that still reads like help — which is worse than saying
# nothing. Re-verify whenever this file is touched, and at launch (#26).
#
# Re-verified 2026-08-12 for #48, against the operators' own pages, not from memory:
#   · 988lifeline.org — 988 is current; call or text; 24/7; free and confidential.
#   · anad.org/eating-disorder-helpline — 888-375-7767 is current. NOTE: ANAD publishes its
#     hours as "Monday through Friday from 9:00 AM to 9:00 PM CT". The copy below says
#     10am–10pm ET, which is the SAME WINDOW (9 CT = 10 ET on both ends) and is therefore
#     accurate — but it is a translation, so it will drift silently if ANAD ever moves the
#     hours without moving the zone. Flagged to Toby; left as approved copy rather than
#     re-worded unilaterally.
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


# =====================================================================================
# The coach prompt
# =====================================================================================

# AC row 18: this prompt carries its OWN crisis and not-medical-advice instructions even
# though the gate exists. Second lock, same doctrine as RLS behind the `user_id` filter —
# neither lock may be the only one. The gate is a model and will eventually be wrong; when
# it is, this is what stands between a person and a coaching answer to a crisis.
#
# ⚠️ #48 PROMOTED THAT FROM BELT-AND-BRACES TO THE LAST LINE OF DEFENCE. There used to be a
# third label, and a message the gate found un-fitness-like was diverted to a fixed string
# without reaching this prompt at all. Now every message that is not classified `crisis`
# reaches Sonnet — so a crisis FALSE NEGATIVE at the gate lands here, and the CRISIS block
# below is the only thing left. It is unchanged in wording, and #48 AC row 15 now tests the
# BEHAVIOUR live (tests/test_coach_live_model.py) rather than only asserting the substring,
# because a substring is exactly the kind of check that let #48's bug ship.
COACH_SYSTEM_PROMPT = """\
You are Coach Bill: a strength and conditioning coach reading one person's daily check-in.

You are given their goal, their recent check-ins, their computed trends, and your own last \
few replies. Everything you know about them is in that context — you have no other memory.

WHAT THIS APP IS, SO YOU CAN BE HONEST ABOUT IT
Coach Bill is a check-in app. Each day the person writes one check-in in plain language. \
The app pulls the hard numbers out of it — sets, reps, weight, food and macros, sleep \
hours, bodyweight — and stores them. They can see today's check-in, a history of past days, \
and a trends dashboard: training volume per day, a per-exercise summary, sleep, bodyweight \
and calories over the last month.

You reply to one check-in, once. That is the whole of what you can do.
- You cannot save a program, put anything in the dashboard, set a reminder, schedule \
anything, or change any screen. Nothing you write is stored as a plan.
- You cannot see photos, wearables, or anything they did not type into a check-in.
- If they ask for something the app cannot do, say so plainly in one sentence and then give \
them what you actually can: the advice itself, in this reply, for them to use.

WHEN SOMETHING ISN'T ABOUT TRAINING
- A stray question or a bit of small talk: answer it in one line, like a person would, then \
bring it back to their training. Do not lecture them about what you are for.
- Something that is real work and not yours — code, essays, emails, homework: say plainly \
in one sentence that it is not what you are for, and move on. No apology paragraph, no \
list of what you do instead.

HOW YOU ANSWER
- A daily check-in report gets two to four sentences. That is the common case and it stays \
short.
- A real question deserves a real answer — up to about eight sentences. Still no article.
- Never pad. If two sentences answer it, write two.
- Reference their ACTUAL numbers from the context. "Third session over 5,000 kg this week" \
beats "great job staying consistent". If the context is empty because this is their first \
check-in, say something useful about what they just logged and do not pretend to see \
history you don't have.
- Never invent a number, a lift, or a trend that isn't in the context. If you don't know, \
ask or leave it out.
- Your last few replies are in the context so you don't repeat yourself word for word. Say \
something new.
- Plain text. No markdown, no headings, no bullet lists, no emoji — even when the answer is \
longer.
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


def _text_of(response: Message) -> str:
    """The reply text, or raise. The validation seam `messages.parse` gives us for free.

    Two failures, both of which must be errors rather than a stored string:

    * **max_tokens** (AC row 34). A reply cut off mid-sentence is not a short reply, it is a
      broken one — and row 2's get-or-create means whatever we store is what the user sees
      forever. Better a 503 they can retry than a permanent half-sentence.
    * **empty** (AC row 35). Same doctrine as extractor.py: untrusted output that produced
      nothing is a failure, never an empty success.

    TYPED AS `Message`, NOT `object`. The first version took `object` and reached for
    `getattr(response, "stop_reason", None)`, which type-checks clean precisely because
    mypy cannot verify a single one of those attribute names. That is worse than an
    explicit `Any` here, because the oracle DELIBERATELY leaves row 34's detection half
    uncovered (a truncated reply is just a string at the `Coach` seam, so no test reaches
    this branch) — types were the only remaining guard, and `object` switched them off. A
    rename or a typo would have made the truncation check a silent no-op, and a half-
    sentence would be stored permanently with no `update` grant to repair it. Narrowing on
    `block.type == "text"` gives `block.text` for free from the discriminated union, the
    same way extractor.py reads typed attributes.
    """
    if response.stop_reason == "max_tokens":
        raise ValueError(
            "the coach's reply hit max_tokens and was truncated mid-sentence; refusing to "
            "store a partial reply (AC row 34)"
        )

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise ValueError(f"the coach returned no usable text (stop: {response.stop_reason})")
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
