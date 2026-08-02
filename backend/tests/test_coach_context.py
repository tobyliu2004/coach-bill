"""Regression tests for the context builder's number formatting (issue #21, PR #47 review).

NOT part of the oracle. `tests/test_coach.py` is commit #1 on this branch and is frozen; a
regression test written after the implementation cannot be an oracle for it, and adding
these there would have edited a file whose whole value is that it hasn't been edited. So
they live here, clearly labelled as what they are: proof that a bug found in review is
fixed, and a tripwire so it cannot come back.

The bug: `_num` expanded integer Decimals with `quantize(Decimal(1))`, which raises
`InvalidOperation` as soon as the result needs more digits than the decimal context's
precision (28). `workout_sets.weight_kg` is `numeric check (weight_kg >= 0)` with NO upper
bound, so `sum(reps * weight_kg)` can exceed that — and the exception escaped
`CoachUnavailable`, surfacing as an uncaught 500 instead of a 503. Worse, it was sticky: the
user's reply endpoint would break on every request until they deleted the offending
check-in.
"""

from decimal import Decimal

import pytest

from app.services.coach import _num


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("135.00", "135"),  # trailing zeros dropped — the reason normalize() is here
        ("135", "135"),
        ("100", "100"),  # normalize() renders this '1E+2'; it must not reach the context
        ("1000000", "1000000"),
        ("0", "0"),  # NOT '' and not '0E+0' — zero is a real, printable answer
        ("61.235", "61.235"),  # fractional precision preserved exactly, never rounded
        ("0.5", "0.5"),
        ("-42", "-42"),  # sign survives the manual expansion
    ],
)
def test_num_formats_without_scientific_notation(raw: str, expected: str) -> None:
    assert _num(Decimal(raw)) == expected


@pytest.mark.parametrize("raw", ["1E+30", "1E+100", "9" * 40])
def test_num_survives_values_larger_than_the_decimal_context_precision(raw: str) -> None:
    """THE REGRESSION. `quantize(Decimal(1))` raised `InvalidOperation` on all of these.

    Reachable from user data: nothing bounds `weight_kg` above, so a single absurd logged
    load produces a volume total big enough to trip it. Asserting "does not raise" AND that
    the output is plain digits — scientific notation in a coaching prompt would be a number
    the model could misread as badly as a crash.
    """
    formatted = _num(Decimal(raw))

    assert "E" not in formatted and "e" not in formatted, formatted
    assert formatted.lstrip("-").isdigit(), formatted
    assert Decimal(formatted) == Decimal(raw)  # exact, not approximated


def test_num_passes_non_finite_values_through_without_raising() -> None:
    """Postgres `numeric` permits NaN, so `sum()` can hand one back.

    NaN has a non-int exponent, which is why the expansion is guarded by an isinstance
    check rather than assuming `as_tuple()` always yields an int. Rendering the string
    'NaN' into the context is not great, but it is a long way better than a 500 — and it is
    the honest representation of what the database actually returned.
    """
    assert _num(Decimal("NaN")) == "NaN"
