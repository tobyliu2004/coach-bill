"""Aggregate queries behind GET /trends. Five reads, one transaction, one window.

⚠️ THE RULE THIS MODULE EXISTS TO GET RIGHT — READ BEFORE EDITING ANY STATEMENT BELOW.

Every fact table is user-owned, and every one of them has to join `check_ins` to learn
which day a row belongs to (`entry_date` lives there, not on the fact). `check_ins` is ALSO
user-owned. So each statement has **two** user-owned tables in it and needs the owner filter
on **both**, bound to the same `$1`:

    join public.check_ins ci on ci.id = ws.check_in_id and ci.user_id = $1   -- and
   where ws.user_id = $1 and ci.entry_date between $2 and $3

Fencing only the fact side is not a smaller mistake, it is the whole bug: anyone's check-in
could then supply the date a row is bucketed under, so another user's calendar would decide
the shape of this user's chart.

And **the existing tripwire cannot see it.** `tests/test_data_isolation.py` regex-searches a
whole statement for the word `user_id`; the fact side's filter already satisfies that, so a
half-filtered join passes it silently. That is why AC row 25 asserts the *contiguous*
clauses on both sides — `<alias>.user_id = $1`, as one substring — rather than trusting the
guard that merely proves the word appears somewhere. `test_trends.py` self-tests that
assertion against a one-sided join so the new guard is one we have watched fail.

An aggregate leak is also the hardest kind to notice: it comes back as a *number*, not as an
id you can recognise as someone else's. That is why AC row 22 is value-shaped.

Two more conventions that are load-bearing rather than stylistic:
  * **Adjacent string literals, never a runtime `+` or `.join()`.** The tripwire parses this
    file's AST; Python merges adjacent literals into one constant, but a runtime concat is
    analysed as fragments and each fragment looks like an unguarded statement.
  * **Always qualify `public.`** — an unqualified table name is invisible to the tripwire.
"""

from datetime import date
from uuid import UUID

import asyncpg

from app.db.session import authed_conn

# --- volume -------------------------------------------------------------------------
# `sum(reps * weight_kg)` returns NULL, not 0, when every set that day was unweighted:
# SQL's sum skips NULLs, and a sum over nothing is NULL. That is exactly the semantic AC
# row 11 wants — but it arrives by accident, so it is pinned by a test and documented here.
# Read `coalesce(..., 0)` onto it and a pushups day silently becomes "0 kg lifted".
#
# The `filter` clauses are what keep bodyweight work visible instead of vanishing into that
# NULL: the day still appears in the series (AC row 12) carrying its own two counters.
# `bodyweight_reps` IS coalesced to 0 — it is a count of reps that really did happen, so
# zero is the truthful answer when none were unweighted.
_VOLUME_SQL = (
    "select ci.entry_date, "
    "       sum(ws.reps * ws.weight_kg) as volume_kg, "
    "       count(*) filter (where ws.weight_kg is null) as bodyweight_sets, "
    "       coalesce(sum(ws.reps) filter (where ws.weight_kg is null), 0) as bodyweight_reps "
    "  from public.workout_sets ws "
    "  join public.check_ins ci on ci.id = ws.check_in_id and ci.user_id = $1 "
    " where ws.user_id = $1 and ci.entry_date between $2 and $3 "
    " group by ci.entry_date "
    " order by ci.entry_date"
)

# --- per-exercise -------------------------------------------------------------------
# Grouped by the CANONICAL name. `workout_sets.exercise_id` already points at the canonical
# row (#19's `resolve_exercise` stores `coalesce(canonical_id, id)`), so a window mixing
# "curls" and "barbell curl" is one row, not two (AC row 20).
#
# `count(*)`/`sum(reps)` count every set; `sum(reps * weight_kg)`/`max(weight_kg)` skip the
# unweighted ones for free, which is the asymmetry AC row 21 pins.
#
# `desc nulls last` is NOT decoration: Postgres sorts DESC as NULLS FIRST by default, so
# without it the bodyweight-only movements (volume NULL) would head the table — the least
# measurable work displacing the heaviest. Reps then name break the ties, so the order is
# total and the screen never jitters between two equal rows (AC row 19).
_EXERCISES_SQL = (
    "select e.name, "
    "       count(*) as sets, "
    "       sum(ws.reps) as reps, "
    "       sum(ws.reps * ws.weight_kg) as volume_kg, "
    "       max(ws.weight_kg) as heaviest_kg "
    "  from public.workout_sets ws "
    "  join public.check_ins ci on ci.id = ws.check_in_id and ci.user_id = $1 "
    "  join public.exercises e on e.id = ws.exercise_id "
    " where ws.user_id = $1 and ci.entry_date between $2 and $3 "
    " group by e.name "
    " order by sum(ws.reps * ws.weight_kg) desc nulls last, sum(ws.reps) desc, e.name"
)

# --- sleep / bodyweight ---------------------------------------------------------------
# `distinct on (ci.entry_date) ... order by ci.entry_date, created_at desc` is latest-wins
# AND oldest-day-first in a single pass: DISTINCT ON keeps the first row of each group under
# the ORDER BY, so the leading `ci.entry_date` gives the series its ascending order (AC row
# 9) and the trailing `created_at desc` picks the correction (AC rows 15/16).
#
# These two are one-per-day facts, so a second row that day is a correction. Nutrition is
# NOT — see below. The blanket "latest row wins" rule PROGRESS.md recorded is right here and
# wrong there.
_SLEEP_SQL = (
    "select distinct on (ci.entry_date) ci.entry_date, s.hours, s.quality "
    "  from public.sleep_entries s "
    "  join public.check_ins ci on ci.id = s.check_in_id and ci.user_id = $1 "
    " where s.user_id = $1 and ci.entry_date between $2 and $3 "
    " order by ci.entry_date, s.created_at desc"
)

_BODYWEIGHT_SQL = (
    "select distinct on (ci.entry_date) ci.entry_date, b.weight_kg "
    "  from public.bodyweight_entries b "
    "  join public.check_ins ci on ci.id = b.check_in_id and ci.user_id = $1 "
    " where b.user_id = $1 and ci.entry_date between $2 and $3 "
    " order by ci.entry_date, b.created_at desc"
)

# --- nutrition --------------------------------------------------------------------------
# SUMMED, not latest (AC row 17) — the one place the latest-wins rule would be wrong.
# Breakfast, lunch and dinner are three rows on one day by design, frequently across three
# separate check-ins. DISTINCT ON here would throw away two meals and report dinner as the
# day's intake: a plausible-looking number that is wrong by most of the day's food.
_NUTRITION_SQL = (
    "select ci.entry_date, "
    "       sum(n.calories) as calories, "
    "       sum(n.protein_g) as protein_g, "
    "       sum(n.carbs_g) as carbs_g, "
    "       sum(n.fat_g) as fat_g "
    "  from public.nutrition_entries n "
    "  join public.check_ins ci on ci.id = n.check_in_id and ci.user_id = $1 "
    " where n.user_id = $1 and ci.entry_date between $2 and $3 "
    " group by ci.entry_date "
    " order by ci.entry_date"
)

_SERIES = (
    ("volume", _VOLUME_SQL),
    ("exercises", _EXERCISES_SQL),
    ("sleep", _SLEEP_SQL),
    ("bodyweight", _BODYWEIGHT_SQL),
    ("nutrition", _NUTRITION_SQL),
)


async def fetch_trends(
    pool: asyncpg.Pool, user_id: UUID, start: date, end: date
) -> dict[str, list[asyncpg.Record]]:
    """Every series for the caller's window, keyed by series name.

    All five reads share ONE `authed_conn` — the shape `list_facts_for_check_ins` already
    established. That is not just a round-trip saving: one transaction means the five series
    are read from a single consistent snapshot, so a check-in landing mid-request can never
    produce a dashboard whose volume chart includes a session its exercise table doesn't.

    Each statement takes exactly `($1 user_id, $2 start, $3 end)`, so the window is bound
    once by the service and cannot drift between series.

    Aggregation happens in Postgres, not in Python. Pulling every set of a 365-day window
    over the wire to sum it here would be slower, would grow with the user's history, and
    would move the `group by` somewhere the isolation tripwire cannot read it.
    """
    async with authed_conn(pool, user_id) as conn:
        # `or []` normalises the declared return type at the boundary rather than leaving
        # every caller to wonder: asyncpg's fetch yields a list, so this only ever fires for
        # a driver double, and the service downstream is free to just iterate.
        return {name: await conn.fetch(sql, user_id, start, end) or [] for name, sql in _SERIES}
