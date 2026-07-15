"""Pure unit test for the local-date transform, written before app/time.py exists.

Covers AC row 6 (the timezone trap) and the None -> UTC fallback in the contract:

    def local_today(tz: str | None) -> date
        # datetime.now(ZoneInfo(tz or "UTC")).date(); None -> UTC

`local_today` reads the wall clock, so we cannot pin "now" by waiting. Instead we patch
the module's clock source (`app.time.datetime`) with a Mock whose `.now(tz)` returns a
FIXED aware instant converted into the requested zone, and prove the *transform*: for the
06:30-UTC instant the calendar date in America/Los_Angeles (UTC-7 in July) is the previous
day. This is fully deterministic — it does not depend on the real current time.
"""

from datetime import UTC, date, datetime, tzinfo
from unittest.mock import patch

# 2026-07-15 06:30 UTC. In America/Los_Angeles (PDT, UTC-7) this is 2026-07-14 23:30,
# i.e. the LA calendar date is ONE DAY BEHIND the UTC date. This is exactly row 6's
# "check-in at 23:30 LA (06:30 UTC next day)" scenario, run in reverse from a fixed instant.
_INSTANT = datetime(2026, 7, 15, 6, 30, tzinfo=UTC)


def _fake_now(tz: tzinfo) -> datetime:
    """Stand-in for datetime.now(tz): the fixed instant, expressed in the passed zone.

    The real function always calls `datetime.now(ZoneInfo(tz or "UTC"))`, so `tz` here is
    always a concrete tzinfo (never None) — the None handling lives inside local_today.
    """
    return _INSTANT.astimezone(tz)


# AC row 6: at the 06:30-UTC instant, local_today("America/Los_Angeles") is the LA date
# (2026-07-14), NOT the UTC date (2026-07-15).
def test_local_today_uses_target_zone_not_utc() -> None:
    from app.time import local_today

    with patch("app.time.datetime") as mock_dt:
        mock_dt.now.side_effect = _fake_now

        la = local_today("America/Los_Angeles")
        utc = local_today("UTC")

    assert la == date(2026, 7, 14)
    assert utc == date(2026, 7, 15)
    # The whole point of row 6: the two dates differ for this instant.
    assert la != utc


# AC row 1 / row 6 fallback: tz=None resolves to UTC (never crashes, never a local drift).
def test_local_today_none_falls_back_to_utc() -> None:
    from app.time import local_today

    with patch("app.time.datetime") as mock_dt:
        mock_dt.now.side_effect = _fake_now

        assert local_today(None) == local_today("UTC")
        assert local_today(None) == date(2026, 7, 15)
