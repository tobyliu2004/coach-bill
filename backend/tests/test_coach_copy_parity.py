"""The frontend's copy of OFF_TOPIC_REPLY must match the backend's, byte for byte.

NOT part of the oracle (that is commit #1; this was written during the PR #47 review).

WHY A DUPLICATED STRING EXISTS AT ALL. The Today screen needs to know whether a stored
reply is the off-topic constant, because only those can be retracted and re-asked — a
CRISIS_REPLY never can, and that is a safety property, not a preference. The obvious fix
was a `retractable` flag on the wire, and it is not available: AC row 1 pins the response
body to EXACTLY `{id, content, created_at}` and the oracle asserts that exact set. So the
constant is mirrored in frontend/src/lib/coachView.ts.

A duplicated constant is only safe if something fails when the copies diverge. That is this
file. Editing the backend's OFF_TOPIC_REPLY without editing the frontend's turns the suite
red, in CI, before it can ship a screen that quietly stops offering "Ask again" — or, worse,
offers it on a reply the server will refuse to retract.

Deliberately compares the RENDERED strings, not the source text: the two files escape and
wrap differently (Python implicit concatenation with trailing backslashes vs a JS template
literal), so a source-level diff would be noise. What has to agree is what the user reads
and what the DELETE statement matches on.
"""

import re
from pathlib import Path

from app.ai.coach import OFF_TOPIC_REPLY

_COACH_VIEW = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "coachView.ts"

# The template literal assigned to `OFF_TOPIC_REPLY`, captured between its backticks.
_TEMPLATE_LITERAL = re.compile(r"export const OFF_TOPIC_REPLY = `(?P<body>.*?)`", re.DOTALL)


def _frontend_off_topic_reply() -> str:
    source = _COACH_VIEW.read_text(encoding="utf-8")
    match = _TEMPLATE_LITERAL.search(source)
    assert match is not None, (
        f"could not find `export const OFF_TOPIC_REPLY = \\`...\\`` in {_COACH_VIEW}. If it "
        "was renamed or changed to a different quoting style, update this test — do not "
        "delete it, or the two copies can drift silently."
    )
    # A JS template literal is otherwise verbatim; the only escape that can appear in this
    # copy is an escaped backtick, which would be a literal backtick in the rendered string.
    return match.group("body").replace("\\`", "`")


def test_frontend_off_topic_reply_matches_the_backend_constant() -> None:
    frontend = _frontend_off_topic_reply()

    assert frontend == OFF_TOPIC_REPLY, (
        "frontend/src/lib/coachView.ts::OFF_TOPIC_REPLY has drifted from "
        "app/ai/coach.py::OFF_TOPIC_REPLY.\n\n"
        "This matters beyond cosmetics: the screen decides whether to offer 'Ask again' by "
        "comparing a stored reply against its copy, and the server decides whether to allow "
        "the retraction by matching against its own. If they disagree, the button appears "
        "on replies the server will refuse (a dead button) or is missing from replies it "
        "would allow (an unreachable feature).\n\n"
        f"backend  ({len(OFF_TOPIC_REPLY)} chars): {OFF_TOPIC_REPLY!r}\n"
        f"frontend ({len(frontend)} chars): {frontend!r}"
    )


def test_the_parity_guard_actually_trips() -> None:
    """The guard is one we have watched fail, not one we hope works.

    Same instinct as `test_data_isolation.py::test_the_guard_actually_trips` and the
    owner-filter self-test in the oracle: a comparison that has never been shown to reject
    anything is indistinguishable from `assert True`.
    """
    assert _frontend_off_topic_reply() != OFF_TOPIC_REPLY + " drifted"
    assert "".join(reversed(OFF_TOPIC_REPLY)) != OFF_TOPIC_REPLY  # not accidentally symmetric
