"""The intent gate — the one check that runs before Coach Bill.

Every check-in is classified before Bill ever sees it, and the gate asks **exactly one
question: is this person in danger?**

**IT USED TO ASK TWO, AND THE SECOND ONE IS WHAT BROKE IT** (issue #48). Alongside safety it
also asked "is this fitness?", diverting a third label — off-topic — to a fixed brush-off.
That second question is fuzzy, has no clean line, and was defined in the prompt by a list of
examples. So the most on-topic request the app can receive —

    "give me a full plan for the next month to build maximal muscle, im talking wrkouts,
     diert, everything, plan it all out for me, and put it in teh dashboard"

— pattern-matched onto "requests to write code or emails" and came back off-topic. The
model was not wrong; the prompt was. **Deleting the fuzzy question made the gate more
robust, not less**: what is left is a safety question with a real answer, and Bill decides
his own scope the way a person would.

**Two labels, and `crisis` is a first-class one for a reason.** A yes/no "is this fitness?"
gate classifies a message about not eating for three days as *not fitness* and drops it on
the floor with a polite nudge about lifting — the worst possible outcome. No system prompt
is a reliable enough place to make that call for the first time, so the gate routes it to a
fixed, human-written reply *before* any generation happens (AC rows 15/16).

**The coach prompt's own crisis block is now the last line of defence, not belt-and-braces.**
With no off-topic diversion, every non-crisis message reaches Sonnet — so a crisis FALSE
NEGATIVE here means Sonnet is what answers it. `COACH_SYSTEM_PROMPT` carries its own crisis
instruction (#21 AC row 18, and #48 AC row 15 now tests the behaviour live rather than the
substring). Same doctrine as RLS sitting behind the `user_id` filter: neither lock may be
the only one — and this is the day the second lock started carrying real weight.

**Cost is no longer this module's job.** It once was: Sonnet is ~15x Haiku's output price
and the off-topic label existed partly to keep trivia away from it. Nearly every real message was
reaching Sonnet anyway, so the measured difference is small (~$15/mo at 30 daily users) and
total spend belongs behind per-user caps (#26), not behind a classifier that has to be right
about a fuzzy question to save a cent.

**The names stayed, the job narrowed.** `Intent`, `IntentGate`, `GATE_SYSTEM_PROMPT` still
fit a two-label classifier, and a rename would have spread the diff across route, service,
tests and PLAN.md while changing no behaviour.

**This boundary fails CLOSED**, deliberately the opposite of `extractor.py`. Extraction
swallows vendor failures because losing derived facts must never cost a user their words.
Here the inverse holds: if the gate can't answer, we do not know whether this text is a
crisis, and sending unclassified text to the coach would defeat the whole point. The caller
turns any failure into a 503 and stores nothing (AC row 13).
"""

from functools import lru_cache
from typing import Annotated, Literal, Protocol

from anthropic import AsyncAnthropic
from fastapi import Depends
from pydantic import BaseModel

from app.config import get_settings

_MODEL = "claude-haiku-4-5"

# One short label is the entire output. 64 is roomy for `{"label":"crisis"}` and tight
# enough that a runaway is impossible.
_MAX_TOKENS = 64

# The gate runs inside POST /check-ins/{id}/reply, ahead of the coach, so its timeout is
# part of a user-facing latency budget rather than a safety net. Deliberately shorter than
# the coach's 25s: a classification that hasn't come back in 15 seconds is not going to.
# The SDK retries 429/5xx twice by default, so worst-case wall clock is roughly 3x this.
_TIMEOUT_SECONDS = 15.0

# Both labels. A `Literal` rather than a str, so Pydantic rejects anything else at the
# boundary: untrusted model output that didn't validate is a failure, never a default
# (#48 AC row 8). Silently defaulting an unknown label to `coach` would send a crisis message
# straight to Sonnet — and there is no longer a third label to fall back to either, which is
# the point: the only two answers are "this person is in danger" and "they are not".
IntentLabel = Literal["coach", "crisis"]


class Intent(BaseModel):
    """What the gate decided. The structured-output shape the SDK validates against."""

    label: IntentLabel


# No prompt caching, deliberately: Haiku 4.5's minimum cacheable prefix is 4096 tokens and
# this prompt is a few hundred. A `cache_control` marker would silently never cache — all
# cost, no benefit, and a misleading line of code. Same note, same reason, as extractor.py.
# No thinking either: this is a two-way classification, not reasoning.
#
# IT OPENS BY DESCRIBING THE APP, AND THAT IS THE FIX. The version this replaced opened
# straight at the labels and defined each one by a list of examples, so the model had no idea
# what it was classifying FOR and could only pattern-match against those lists. A classifier
# told what the product is can reason about a message it has never seen a example of; one
# handed an example list can only match. See the module docstring for the message that broke.
GATE_SYSTEM_PROMPT = """\
Coach Bill is a fitness check-in app. Each day a person writes one check-in in their own \
words — what they trained, what they ate, how they slept, how they are feeling about any of \
it — and Bill, their coach, writes back.

You are not Bill. You are the one check that runs before him, and you have a single \
question to answer: IS THIS PERSON IN DANGER?

That is all you decide. Give the message EXACTLY ONE label.

Return only the label. Never explain.

LABELS

"crisis" — the message signals danger to the person's health or life. Choose this when \
the text suggests any of:
- suicidal thoughts, self-harm, or not wanting to be here anymore
- disordered eating: purging, vomiting after meals, laxative use, not eating for days, \
extreme restriction, obsessive fear of eating
- exercising to punish themselves, or to "make up for" eating
- abuse, or someone else hurting them

"coach" — EVERYTHING that is not a crisis. You are not judging whether the message is \
about fitness, and you are not judging whether Bill can help with it. A question, a \
request, a complaint, a plain report of a workout, small talk, something with nothing to \
do with training at all: all of it is "coach". Bill decides for himself what he can and \
can't do with a message.

DECIDING BETWEEN THEM

Choose "crisis" over "coach" whenever the crisis signals above are present, even \
if the message is also about fitness. Disordered eating very often arrives dressed as a \
normal fitness question ("is throwing up after meals ok for cutting") — the framing does \
not change what it is.

But do NOT reach for "crisis" on ordinary gym talk. These are all "coach":
- frustration, discouragement, or feeling stuck ("I'm frustrated with my progress")
- pain, injury, and soreness ("my knee hurts", "my back is wrecked")
- figures of speech and hyperbole ("I'm so sore I could die", "that set killed me", \
"my legs are destroyed")
- wanting to lose or gain weight, even impatiently

Judge the person's actual situation, not the presence of dramatic words. A common idiom \
is not a signal. Getting this wrong in the alarming direction has a real cost: a person \
told they are in crisis because their knee aches learns the app overreacts, and stops \
trusting it on the day it matters.

When a message genuinely sits between "crisis" and "coach" — real ambiguity, not just \
strong language — choose "crisis"."""


class IntentGate(Protocol):
    """What the rest of the app is allowed to know about the gate model."""

    async def classify(self, text: str) -> Intent:
        """Classify one check-in's raw text as `crisis` or `coach`.

        Raises on vendor failure or unparseable/invalid output. The caller does NOT catch
        this into a default — it becomes a 503 with nothing stored (AC row 13). A gate that
        fails open is not a gate.
        """
        ...


class HaikuGate:
    """The real `IntentGate` — `claude-haiku-4-5` with structured output.

    `messages.parse` + `output_format` makes the SDK enforce `Intent`, so a label outside
    the two raises `ValidationError` here rather than reaching the router — which is what
    #48 AC row 8 is about, and it is why a stale off-topic label coming back from a cached or
    mis-pinned model is a loud failure rather than a silently-honoured third path. There is
    no hand-rolled string matching on the model's reply to get wrong, and no place for a
    stray "Sure! The label is: coach" to be mis-read.
    """

    def __init__(self, client: AsyncAnthropic | None = None) -> None:
        # Defaults to the shared process-wide client, so `HaikuGate()` just works (that is
        # how the live-model tests construct it). Still injectable.
        self._client = client if client is not None else _client()

    async def classify(self, text: str) -> Intent:
        response = await self._client.messages.parse(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=GATE_SYSTEM_PROMPT,
            output_format=Intent,
            messages=[{"role": "user", "content": text}],
        )
        intent = response.parsed_output
        if intent is None:
            # Structured output produced nothing valid. Raise rather than guess: there is
            # no safe default label, which is the whole point of this module.
            raise ValueError(
                f"the intent gate returned no parsable label (stop: {response.stop_reason})"
            )
        return intent


@lru_cache
def _client() -> AsyncAnthropic:
    """One client for the process — it holds a connection pool; building one per request
    would throw that away. Cached like `get_settings`."""
    return AsyncAnthropic(
        api_key=get_settings().anthropic_api_key,
        timeout=_TIMEOUT_SECONDS,
    )


def get_gate() -> IntentGate:
    """Dependency: hand the route the live gate. Tests override this (see
    `app.dependency_overrides`) so CI never spends a token."""
    return HaikuGate()


GateDep = Annotated[IntentGate, Depends(get_gate)]
