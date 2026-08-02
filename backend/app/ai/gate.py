"""The intent gate — the cheap model standing in front of the expensive one.

Every check-in is classified before Coach Bill ever sees it. That does two jobs at once,
and they are not the same job:

  * **Cost control.** Sonnet is ~15x Haiku's output price. "What's the capital of France"
    must never reach it (AC row 10).
  * **Safety.** A message about not eating for three days is not a coaching prompt, and no
    system prompt is a reliable enough place to make that decision for the first time. The
    gate routes it to a fixed, human-written reply *before* any generation happens
    (AC rows 15/16).

**Three labels, not two.** A yes/no "is this fitness?" gate would classify a crisis message
as "not fitness" and drop it on the floor with a polite nudge about lifting — the worst
possible outcome, and the reason `crisis` is a first-class label rather than something the
coach prompt is trusted to notice. The coach prompt carries its own crisis instruction too
(AC row 18): the same doctrine as RLS sitting behind the `user_id` filter, where neither
lock is allowed to be the only one.

**This boundary fails CLOSED**, deliberately the opposite of `extractor.py`. Extraction
swallows vendor failures because losing derived facts must never cost a user their words.
Here the inverse holds: if the gate can't answer, we do not know whether this text is a
crisis, and sending unclassified text to the coach would defeat both jobs above. The caller
turns any failure into a 503 and stores nothing (AC row 13).
"""

from functools import lru_cache
from typing import Annotated, Literal, Protocol

from anthropic import AsyncAnthropic
from fastapi import Depends
from pydantic import BaseModel

from app.config import get_settings

_MODEL = "claude-haiku-4-5"

# One short label is the entire output. 64 is roomy for `{"label":"off_topic"}` and tight
# enough that a runaway is impossible.
_MAX_TOKENS = 64

# The gate runs inside POST /check-ins/{id}/reply, ahead of the coach, so its timeout is
# part of a user-facing latency budget rather than a safety net. Deliberately shorter than
# the coach's 25s: a classification that hasn't come back in 15 seconds is not going to.
# The SDK retries 429/5xx twice by default, so worst-case wall clock is roughly 3x this.
_TIMEOUT_SECONDS = 15.0

# The three labels. A `Literal` rather than a str, so Pydantic rejects anything else at the
# boundary: untrusted model output that didn't validate is a failure, never a default
# (AC row 14). Silently defaulting an unknown label to `coach` would send a crisis message
# to Sonnet; defaulting it to `off_topic` would drop one entirely.
IntentLabel = Literal["coach", "crisis", "off_topic"]


class Intent(BaseModel):
    """What the gate decided. The structured-output shape the SDK validates against."""

    label: IntentLabel


# No prompt caching, deliberately: Haiku 4.5's minimum cacheable prefix is 4096 tokens and
# this prompt is a few hundred. A `cache_control` marker would silently never cache — all
# cost, no benefit, and a misleading line of code. Same note, same reason, as extractor.py.
# No thinking either: this is a three-way classification, not reasoning.
GATE_SYSTEM_PROMPT = """\
You classify one message from a person's fitness check-in app into EXACTLY ONE label.

Return only the label. Never explain.

LABELS

"crisis" — the message signals danger to the person's health or life. Choose this when \
the text suggests any of:
- suicidal thoughts, self-harm, or not wanting to be here anymore
- disordered eating: purging, vomiting after meals, laxative use, not eating for days, \
extreme restriction, obsessive fear of eating
- exercising to punish themselves, or to "make up for" eating
- abuse, or someone else hurting them

"off_topic" — the message has nothing to do with the person's body, training, food, \
sleep, weight, energy, or how any of that is going. General knowledge questions, trivia, \
requests to write code or emails, and chat about unrelated topics are off_topic.

"coach" — everything else. If it is about their training, food, sleep, bodyweight, \
soreness, pain, motivation, or how their progress is going, it is coach.

DECIDING BETWEEN THEM

Choose "crisis" over the other two whenever the crisis signals above are present, even \
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
        """Classify one check-in's raw text into exactly one of the three labels.

        Raises on vendor failure or unparseable/invalid output. The caller does NOT catch
        this into a default — it becomes a 503 with nothing stored (AC row 13). A gate that
        fails open is not a gate.
        """
        ...


class HaikuGate:
    """The real `IntentGate` — `claude-haiku-4-5` with structured output.

    `messages.parse` + `output_format` makes the SDK enforce `Intent`, so a label outside
    the three raises `ValidationError` here rather than reaching the router — which is what
    AC row 14 is about. There is no hand-rolled string matching on the model's reply to get
    wrong, and no place for a stray "Sure! The label is: coach" to be mis-read.
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
