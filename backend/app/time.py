"""Which calendar day is it *for the user*?

"Today" in Coach Bill means the user's local day, never the server's UTC day — a
check-in typed at 23:30 in Los Angeles belongs to that LA date even though the server
clock has already ticked past midnight UTC. This one helper is the single source of that
answer: both the write (POST sets `entry_date`) and the read (GET filters on it) call it,
so they can never disagree on what "today" is.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo


def local_today(tz: str | None) -> date:
    """The current calendar date in IANA zone `tz`; `None` falls back to UTC.

    A NULL `profiles.timezone` is a rare seatbelt — onboarding auto-captures the browser
    tz — so we degrade to UTC rather than block the core flow. `tz` is trusted: it was
    validated against zoneinfo when it was written (see schemas/profiles.py).
    """
    return datetime.now(ZoneInfo(tz or "UTC")).date()
