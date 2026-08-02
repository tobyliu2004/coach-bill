/**
 * Oracle suite for issue #20, PR 1 (History) — frontend rows 21-25.
 *
 * Commit #1 on `feat/20-history`, written BEFORE any implementation exists. `./history`
 * does not exist yet: this file failing to resolve it is the CORRECT initial failure, and
 * no stub was created to make the import go green.
 *
 * Why a new pure module rather than tests on the /history screen: vitest here is node env,
 * `src/**` only, no DOM (vite.config.ts), so a `.tsx` component is untestable. Rows 21-25
 * are all pure DECISIONS — which state is this, which day does this row belong to, what do
 * we call that day — so they live in `lib/history.ts` and the screen renders what it
 * returns. Same split `checkInView.ts` already established after the #18 retro, whose
 * stated gap was that the correctness table had no frontend/error-state dimension.
 */
import { describe, expect, it } from 'vitest'
import { ApiAuthError, ApiError, type CheckIn } from './api'
import { errorAction } from './checkInView'
import { dayLabel, groupByDay, historyView, localToday, type DayGroup } from './history'

const NO_FACTS: CheckIn['facts'] = { sets: [], nutrition: [], sleep: [], bodyweight: [] }

function checkIn(id: string, entry_date: string, created_at: string): CheckIn {
  return {
    id,
    raw_text: `check-in ${id}`,
    source: 'text',
    entry_date,
    created_at,
    extraction_status: 'done',
    facts: NO_FACTS,
    // #21 bundled Bill's reply onto every check-in; these fixtures are about grouping and
    // day labels, so `null` is the honest value. No assertion here reads it.
    reply: null,
  }
}

// Five check-ins across three DISTINCT entry_dates, in the order the API returns them
// (backend row 10: newest entry_date first, newest created_at first within a day). The
// dates are deliberately NON-CONSECUTIVE — Aug 1, Jul 30, Jul 25 — so an implementation
// that walks the calendar and emits a group per date in the range produces 8 groups here,
// not 3, and row 24's "days with no check-ins are omitted entirely" actually bites.
const AUG1_LATE = checkIn('a1', '2026-08-01', '2026-08-01T19:00:00Z')
const AUG1_EARLY = checkIn('a2', '2026-08-01', '2026-08-01T07:30:00Z')
const JUL30_LATE = checkIn('b1', '2026-07-30', '2026-07-30T21:15:00Z')
const JUL30_EARLY = checkIn('b2', '2026-07-30', '2026-07-30T06:05:00Z')
const JUL25 = checkIn('c1', '2026-07-25', '2026-07-25T12:00:00Z')

const API_ORDER: CheckIn[] = [AUG1_LATE, AUG1_EARLY, JUL30_LATE, JUL30_EARLY, JUL25]

describe('groupByDay', () => {
  // AC row 24: 5 check-ins across 3 distinct entry_dates -> exactly 3 groups, newest day
  // first, items newest-first within each day, and NO group for a day nobody logged. A
  // rendered empty day is a lie — you didn't log nothing, you didn't log.
  it('produces one group per distinct entry_date, newest day first', () => {
    expect(groupByDay(API_ORDER)).toEqual([
      { date: '2026-08-01', checkIns: [AUG1_LATE, AUG1_EARLY] },
      { date: '2026-07-30', checkIns: [JUL30_LATE, JUL30_EARLY] },
      { date: '2026-07-25', checkIns: [JUL25] },
    ])
  })

  // AC row 24 (the omission half, asserted on its own): the gap days between Jul 25 and
  // Aug 1 must not appear at all. The count assertion is what a calendar-walking
  // implementation fails; asserting the shape above alone could be read as "these three
  // are present" rather than "only these three".
  it('omits days with no check-ins entirely', () => {
    const groups: DayGroup[] = groupByDay(API_ORDER)

    expect(groups).toHaveLength(3)
    expect(groups.map((g: DayGroup) => g.date)).toEqual(['2026-08-01', '2026-07-30', '2026-07-25'])
    expect(groups.every((g: DayGroup) => g.checkIns.length > 0)).toBe(true)
  })

  // AC row 24 (the degenerate ends): nothing in, nothing out — and one check-in is one
  // group of one, not a bare list.
  it('returns no groups for no check-ins', () => {
    expect(groupByDay([])).toEqual([])
  })

  it('returns a single group for a single check-in', () => {
    expect(groupByDay([JUL25])).toEqual([{ date: '2026-07-25', checkIns: [JUL25] }])
  })
})

describe('dayLabel', () => {
  // AC row 25: entry_date == the user's today -> 'Today'.
  it("labels the user's today as 'Today'", () => {
    expect(dayLabel('2026-08-01', '2026-08-01')).toBe('Today')
  })

  // AC row 25: the day before -> 'Yesterday'. Note Jul 31 -> Aug 1 crosses a month, which
  // is where naive day-number arithmetic ('01' - 1 = '00') breaks.
  it("labels the day before as 'Yesterday', across a month boundary", () => {
    expect(dayLabel('2026-07-31', '2026-08-01')).toBe('Yesterday')
  })

  // AC row 25 (the same boundary, one notch harder): yesterday across a YEAR boundary is
  // still yesterday, not a formatted date from last year.
  it("labels the day before as 'Yesterday', across a year boundary", () => {
    expect(dayLabel('2025-12-31', '2026-01-01')).toBe('Yesterday')
  })

  // AC row 25: older, same calendar year -> the month/day, no year. Format pinned so it is
  // deterministic rather than whatever the runner's locale happens to produce.
  it('labels an older day in the same year as month + day', () => {
    expect(dayLabel('2026-07-25', '2026-08-01')).toBe('Jul 25')
  })

  // AC row 25: older, DIFFERENT calendar year -> the year is carried, because 'Dec 31'
  // eight months into the next year is ambiguous.
  it('labels an older day in a different year with the year', () => {
    expect(dayLabel('2025-12-31', '2026-08-01')).toBe('Dec 31, 2025')
  })

  // AC row 25: two days back is NOT 'Yesterday'. Without this, an off-by-one that labels
  // everything recent as 'Yesterday' passes every assertion above.
  it('does not call the day before yesterday anything special', () => {
    expect(dayLabel('2026-07-30', '2026-08-01')).toBe('Jul 30')
  })
})

describe('localToday', () => {
  // AC row 25 (the "user's local today" half): at 2026-08-02 00:30 UTC it is still
  // 2026-08-01 in Los Angeles. Using the browser's raw Date here is what reintroduces the
  // #18 timezone bug on the label — the user's evening check-in gets called 'Yesterday'.
  it("returns the user's local date, not the UTC date, for a zone behind UTC", () => {
    expect(localToday('America/Los_Angeles', new Date('2026-08-02T00:30:00Z'))).toBe('2026-08-01')
  })

  // AC row 25 (the other direction): a zone AHEAD of UTC is already tomorrow. A helper
  // that only ever subtracts would pass the LA case and fail this one.
  it('returns tomorrow for a zone ahead of UTC', () => {
    expect(localToday('Asia/Tokyo', new Date('2026-08-01T23:00:00Z'))).toBe('2026-08-02')
  })

  // AC row 25 + backend row 4: a null timezone falls back to UTC — the same seatbelt the
  // server takes, so the label and the window can never disagree about what day it is.
  it('falls back to UTC when the profile has no timezone', () => {
    expect(localToday(null, new Date('2026-08-02T00:30:00Z'))).toBe('2026-08-02')
  })

  // AC row 25 (end to end, the actual bug): an LA user's 17:30 check-in on Aug 1, read at
  // 00:30 UTC on Aug 2, is labelled 'Today'. This is the composition the screen performs,
  // and it is the assertion that fails if either half quietly uses the server's date.
  it("labels an LA user's evening check-in 'Today' after UTC midnight", () => {
    const today = localToday('America/Los_Angeles', new Date('2026-08-02T00:30:00Z'))

    expect(dayLabel('2026-08-01', today)).toBe('Today')
  })
})

describe('historyView', () => {
  // AC row 21: the history fetch throws a non-auth error -> 'load-failed', NEVER 'empty'.
  // This is the exact bug the #18 reviewers caught: a failed fetch rendering as "you have
  // no check-ins" reads as data loss.
  it("reports 'load-failed' when the fetch failed", () => {
    expect(historyView({ loading: false, loadFailed: true, checkIns: [] })).toEqual({
      kind: 'load-failed',
    })
  })

  // AC row 22: the fetch SUCCEEDED and there is genuinely nothing -> 'empty'.
  it("reports 'empty' only when the fetch succeeded and returned nothing", () => {
    expect(historyView({ loading: false, loadFailed: false, checkIns: [] })).toEqual({
      kind: 'empty',
    })
  })

  // AC rows 21+22 together: two different truths must not render identically. Asserting
  // each shape separately would still pass an implementation that collapsed both onto one
  // value — which IS the bug — so assert the distinction itself. The two inputs here are
  // identical except for `loadFailed`.
  it('distinguishes a failed load from a genuinely empty history', () => {
    const failed = historyView({ loading: false, loadFailed: true, checkIns: [] })
    const empty = historyView({ loading: false, loadFailed: false, checkIns: [] })

    expect(failed.kind).not.toBe(empty.kind)
  })

  // AC row 21 (before the fetch settles): loading is its own state, not an empty history.
  it("reports 'loading' before the first fetch settles", () => {
    expect(historyView({ loading: true, loadFailed: false, checkIns: [] })).toEqual({
      kind: 'loading',
    })
  })

  // AC rows 22+24: a successful fetch with check-ins renders the DAYS, grouped. Without
  // this, a historyView that returned 'empty' unconditionally would satisfy row 22.
  it('reports the grouped days when the fetch returned check-ins', () => {
    expect(historyView({ loading: false, loadFailed: false, checkIns: API_ORDER })).toEqual({
      kind: 'days',
      days: [
        { date: '2026-08-01', checkIns: [AUG1_LATE, AUG1_EARLY] },
        { date: '2026-07-30', checkIns: [JUL30_LATE, JUL30_EARLY] },
        { date: '2026-07-25', checkIns: [JUL25] },
      ],
    })
  })
})

describe('errorAction on the history fetch', () => {
  // AC row 23: an ApiAuthError from the history fetch is a SIGN-OUT, not an in-screen
  // message — anything else strands the user on a screen that can never succeed. The row
  // says reuse `errorAction` from checkInView.ts, so this asserts the imported function
  // rather than reimplementing the decision in a second place where it could drift.
  it('maps ApiAuthError to sign-out', () => {
    expect(errorAction(new ApiAuthError('session rejected by the API'))).toEqual({
      kind: 'sign-out',
    })
  })

  // AC rows 21+23 (the boundary between them): a non-auth failure is the in-app message
  // path, which is what row 21's 'load-failed' state renders. Signing the user out on a
  // 500 would be its own bug.
  it('maps a non-auth API failure to an in-app message, not a sign-out', () => {
    expect(errorAction(new ApiError(500, '/check-ins?days=30 failed'))).toEqual({
      kind: 'message',
    })
  })
})

// =======================================================================================
// Issue #20, PR 2 (Trends) — rows 28 and 29. Also closes #40.
//
// APPENDED, never edited: every line above is PR 1's frozen oracle and stays byte-identical.
// A second `import` from './history' rather than a change to the one at the top of the file,
// for the same reason — ESM hoists both, and the diff against the oracle commit has to show
// additions only.
//
// Why these two rows are here at all: #40 is that the screen-wiring seam — "which window,
// computed from whose today" — is untested on all three screens. Row 28 pins History's half
// of that seam; row 29 pins the `catch` in `localToday` that every one of them falls back
// through.
// =======================================================================================
import { HISTORY_DAYS, historyRequest } from './history'

describe('historyRequest', () => {
  // AC row 28 (#40, the History screen's half): the same input as row 27 must produce
  // `days: 30` and the SAME Los Angeles today — at 2026-08-02 00:30 UTC that is
  // '2026-08-01'. This is the call the /history screen makes; if it is ever rewritten to
  // compute today from the browser's clock, `today` becomes '2026-08-02' and this fails.
  it("asks for 30 days ending on the user's local today", () => {
    expect(
      historyRequest({ timezone: 'America/Los_Angeles' }, new Date('2026-08-02T00:30:00Z')),
    ).toEqual({ days: 30, today: '2026-08-01' })
  })

  // AC row 28: the window is the exported constant, and it is 30 — the same span /trends
  // asks for, so the two screens can never describe different months (backend row 1).
  it('is built from the exported HISTORY_DAYS constant, which is 30', () => {
    expect(HISTORY_DAYS).toBe(30)
    expect(historyRequest({ timezone: 'UTC' }, new Date('2026-08-01T12:00:00Z')).days).toBe(
      HISTORY_DAYS,
    )
  })

  // AC row 28 (the seatbelt, shared with backend row 4): no profile yet, or a profile with
  // no timezone, computes the window in UTC rather than throwing mid-render.
  it('falls back to UTC when there is no profile or no timezone', () => {
    expect(historyRequest(null, new Date('2026-08-02T00:30:00Z'))).toEqual({
      days: 30,
      today: '2026-08-02',
    })
    expect(historyRequest({ timezone: null }, new Date('2026-08-02T00:30:00Z'))).toEqual({
      days: 30,
      today: '2026-08-02',
    })
  })
})

describe('localToday — the unknown-zone seatbelt', () => {
  // AC row 29 (#40's fold-in): an IANA zone this browser has never heard of makes
  // `Intl.DateTimeFormat` throw a RangeError. That `catch` is the last untested seatbelt in
  // this module, and an uncaught throw here white-screens the app mid-render — so the
  // fallback is UTC, exactly what the server's `local_today` does for a missing zone.
  it('falls back to UTC for a zone this runtime does not know, without throwing', () => {
    expect(() => localToday('Mars/Olympus_Mons', new Date('2026-08-02T00:30:00Z'))).not.toThrow()
    expect(localToday('Mars/Olympus_Mons', new Date('2026-08-02T00:30:00Z'))).toBe('2026-08-02')
  })

  // AC row 29 (the distinguishing half): the fallback is UTC specifically, not "whatever
  // the runner's zone happens to be". Asserting only the LA-evening instant above would
  // pass on a machine set to UTC either way; this instant is unambiguous in UTC.
  it('produces the UTC date, not the runtime default zone, for an unknown zone', () => {
    expect(localToday('Not/AZone', new Date('2026-01-01T00:05:00Z'))).toBe('2026-01-01')
  })
})
