/**
 * Oracle suite for issue #20, PR 2 (Trends) — frontend rows 27, 31, 32, 33, 34
 * (plus the client half of row 8, and decision 2's "the axis comes from the server").
 *
 * Part of commit #1 on `feat/20-trends`, written BEFORE any implementation exists.
 * `./trends` and `./dates` are not on disk and `Trends` is not exported from `./api`:
 * those unresolved imports are the CORRECT initial failure, and no stub was created to
 * make them go green.
 *
 * Why these live in `lib/` and not in the screen: vitest here is node env over `src/**`
 * with no DOM (vite.config.ts), so a `.tsx` component cannot be mounted. Every row below
 * is a pure DECISION — which state is this, where does this bar go, is this number a
 * number — so it lives here and the screen renders what it returns. Same split
 * `checkInView.ts` and `history.ts` already established, and the same reason: the #18 and
 * #39 bugs were all in the decision, never in the markup.
 */
import { describe, expect, it } from 'vitest'
import { ApiAuthError, ApiError, type Trends } from './api'
import { errorAction } from './checkInView'
import {
  barLayout,
  parseNumeric,
  sparkline,
  TRENDS_DAYS,
  trendsRequest,
  trendsView,
} from './trends'

const EMPTY_TRENDS: Trends = {
  start_date: '2026-07-03',
  end_date: '2026-08-01',
  volume: [],
  exercises: [],
  sleep: [],
  bodyweight: [],
  nutrition: [],
}

const TRENDS_WITH_DATA: Trends = {
  ...EMPTY_TRENDS,
  volume: [
    { date: '2026-07-28', volume_kg: '3200', bodyweight_sets: 0, bodyweight_reps: 0 },
    { date: '2026-08-01', volume_kg: null, bodyweight_sets: 3, bodyweight_reps: 30 },
  ],
  exercises: [
    { name: 'squat', sets: 12, reps: 96, volume_kg: '9180', heaviest_kg: '140' },
    { name: 'pushups', sets: 3, reps: 30, volume_kg: null, heaviest_kg: null },
  ],
}

describe('trendsRequest', () => {
  // AC row 27 (#40's core row): at 2026-08-02 00:30 UTC it is still 2026-08-01 in Los
  // Angeles, so the dashboard asks for 30 days ending on the USER's date. Both halves of
  // the returned object are the assertion: change the call site to compute today from the
  // browser's zone and `today` becomes '2026-08-02'; change the constant and `days` moves.
  it("asks for 30 days ending on the user's local today, not the browser's", () => {
    expect(
      trendsRequest({ timezone: 'America/Los_Angeles' }, new Date('2026-08-02T00:30:00Z')),
    ).toEqual({ days: 30, today: '2026-08-01' })
  })

  // AC row 27 (the other direction): a zone AHEAD of UTC is already tomorrow. A helper
  // that only ever subtracts passes the LA case and fails this one.
  it("uses the user's zone when it is ahead of UTC", () => {
    expect(trendsRequest({ timezone: 'Asia/Tokyo' }, new Date('2026-08-01T23:00:00Z'))).toEqual({
      days: 30,
      today: '2026-08-02',
    })
  })

  // AC row 27 + the UTC seatbelt rows 4/29 pin on both sides of the wire: no profile at
  // all (the fetch has not landed) falls back to UTC rather than throwing or guessing.
  it('falls back to UTC when there is no profile yet', () => {
    expect(trendsRequest(null, new Date('2026-08-02T00:30:00Z'))).toEqual({
      days: 30,
      today: '2026-08-02',
    })
  })

  // AC row 27: the same seatbelt for a profile whose timezone was never captured.
  it('falls back to UTC when the profile has no timezone', () => {
    expect(trendsRequest({ timezone: null }, new Date('2026-08-02T00:30:00Z'))).toEqual({
      days: 30,
      today: '2026-08-02',
    })
  })

  // AC row 27 (the constant, named): 30 is exported rather than inlined, and it matches
  // /history's window so the two screens describe the same span (backend row 1).
  it('is built from the exported TRENDS_DAYS constant, which is 30', () => {
    expect(TRENDS_DAYS).toBe(30)
    expect(trendsRequest({ timezone: 'UTC' }, new Date('2026-08-01T12:00:00Z')).days).toBe(
      TRENDS_DAYS,
    )
  })
})

describe('trendsView', () => {
  // AC row 31: the fetch threw a non-auth error -> 'load-failed', NEVER 'empty'. The #18
  // and #39 bug one screen further on: "you have no data" when we simply could not ask
  // reads as data loss.
  it("reports 'load-failed' when the fetch failed", () => {
    expect(trendsView({ loading: false, loadFailed: true, trends: null })).toEqual({
      kind: 'load-failed',
    })
  })

  // AC row 33: the window returned nothing anywhere -> 'empty'. Scoped to the WINDOW: the
  // payload still carries start_date/end_date (backend row 7) so the screen can say which
  // month was empty instead of "you have no data" to someone who took a month off.
  it("reports 'empty' when the fetch succeeded and every series is empty", () => {
    expect(trendsView({ loading: false, loadFailed: false, trends: EMPTY_TRENDS })).toEqual({
      kind: 'empty',
    })
  })

  // AC row 31 (the load-bearing assertion, stated by the row itself): the two kinds must
  // DIFFER. Asserting each shape separately still passes an implementation that collapses
  // them onto one value — which IS the bug. The two inputs differ only in `loadFailed`.
  it('distinguishes a failed load from a genuinely empty window', () => {
    const failed = trendsView({ loading: false, loadFailed: true, trends: null })
    const empty = trendsView({ loading: false, loadFailed: false, trends: EMPTY_TRENDS })

    expect(failed.kind).not.toBe(empty.kind)
  })

  // AC row 31 (before the fetch settles): loading is its own state, not an empty window.
  it("reports 'loading' before the first fetch settles", () => {
    expect(trendsView({ loading: true, loadFailed: false, trends: null })).toEqual({
      kind: 'loading',
    })
  })

  // AC row 33 (the other side): a window WITH data renders the trends. Without this, a
  // trendsView that returned 'empty' unconditionally would satisfy the row above.
  it('reports the trends when the window returned data', () => {
    expect(trendsView({ loading: false, loadFailed: false, trends: TRENDS_WITH_DATA })).toEqual({
      kind: 'trends',
      trends: TRENDS_WITH_DATA,
    })
  })
})

describe('errorAction on the trends fetch', () => {
  // AC row 32: an ApiAuthError from the trends fetch is a SIGN-OUT, not an in-screen
  // message — anything else strands the user on a screen that can never succeed. The row
  // says reuse the existing `errorAction`, so this asserts the imported function rather
  // than a second copy of the decision that could drift from it.
  it('maps ApiAuthError to sign-out', () => {
    expect(errorAction(new ApiAuthError('session rejected by the API'))).toEqual({
      kind: 'sign-out',
    })
  })

  // AC rows 31+32 (the boundary): a non-auth failure is the in-app message path, which is
  // what row 31's 'load-failed' state renders. Signing the user out on a 500 is its own bug.
  it('maps a non-auth API failure to an in-app message, not a sign-out', () => {
    expect(errorAction(new ApiError(500, '/trends?days=30 failed'))).toEqual({ kind: 'message' })
  })
})

describe('parseNumeric', () => {
  // AC row 34 (the guard that keeps NaN out of the geometry): every measure crosses the
  // wire as a Decimal-shaped STRING, and `Number(...)` on anything unexpected yields NaN.
  // A NaN reaching an SVG attribute renders NOTHING — a blank chart that looks like "no
  // data". Parsing therefore happens once, here, and refuses to hand back a non-finite
  // number at all.
  it('parses a decimal string into a number', () => {
    expect(parseNumeric('3200')).toBe(3200)
    expect(parseNumeric('61.235')).toBe(61.235)
  })

  // AC row 34: null is the shape `volume_kg` / `heaviest_kg` take for a bodyweight-only
  // day (backend row 11) — it must stay null, never become 0. Zero tonnage and no tonnage
  // are different facts and would draw different bars.
  it('returns null for a null measure rather than coercing it to 0', () => {
    expect(parseNumeric(null)).toBeNull()
    expect(parseNumeric(null)).not.toBe(0)
  })

  it('returns null for a missing measure', () => {
    expect(parseNumeric(undefined)).toBeNull()
  })

  // AC row 34: anything that does not parse to a finite number is null, never NaN.
  it('returns null instead of NaN for a value that is not a number', () => {
    expect(parseNumeric('not a number')).toBeNull()
    expect(parseNumeric('Infinity')).toBeNull()
  })
})

describe('barLayout', () => {
  // AC row 8 (the CLIENT half of "sparse, not padded") + decision 2: bars are laid out
  // across the SERVER's window, one bar per POINT — never one per window day. Two points
  // in a four-day window is two bars; the gap stays a gap. A layout that padded the window
  // would produce four bars here, two of them lies.
  it('draws one bar per point, not one per day in the window', () => {
    const { bars } = barLayout(
      [
        { date: '2026-07-30', value: 10 },
        { date: '2026-08-01', value: 20 },
      ],
      '2026-07-30',
      '2026-08-02',
    )

    expect(bars).toHaveLength(2)
    expect(bars.map((bar) => bar.date)).toEqual(['2026-07-30', '2026-08-01'])
  })

  // AC row 8 + row 30: a bar's x is its date's INDEX IN THE WINDOW, so a gap day leaves a
  // gap on the axis. Four days -> width 25 each; Jul 30 is index 0 (x 0) and Aug 1 is
  // index 2 (x 50). A layout that packed the points side by side would put the second bar
  // at x 25 and slide the whole month left.
  it('positions each bar at its own date within the window', () => {
    const { bars } = barLayout(
      [
        { date: '2026-07-30', value: 10 },
        { date: '2026-08-01', value: 20 },
      ],
      '2026-07-30',
      '2026-08-02',
    )

    expect(bars[0]).toEqual({ date: '2026-07-30', value: 10, x: 0, y: 50, width: 25, height: 50 })
    expect(bars[1]).toEqual({ date: '2026-08-01', value: 20, x: 50, y: 0, width: 25, height: 100 })
  })

  // AC row 8 + decision 2 ("the client never recomputes today"): a point outside the
  // server's window has no place on the axis and is dropped rather than drawn off-canvas.
  it('drops a point that falls outside the window', () => {
    const { bars } = barLayout(
      [
        { date: '2026-07-29', value: 99 },
        { date: '2026-08-01', value: 20 },
        { date: '2026-08-03', value: 99 },
      ],
      '2026-07-30',
      '2026-08-02',
    )

    expect(bars.map((bar) => bar.date)).toEqual(['2026-08-01'])
  })

  // AC row 34 (all values zero — the divide-by-zero case): max is 0, so scaling would
  // divide by zero and every height would be NaN. A NaN height renders nothing, which on
  // screen is indistinguishable from "no data". Heights must be 0 and finite.
  it('produces finite zero-height bars when every value is zero', () => {
    const { bars, max } = barLayout(
      [
        { date: '2026-07-30', value: 0 },
        { date: '2026-08-01', value: 0 },
      ],
      '2026-07-30',
      '2026-08-02',
    )

    expect(max).toBe(0)
    expect(bars.map((bar) => bar.height)).toEqual([0, 0])
    expect(bars.map((bar) => bar.y)).toEqual([100, 100])
    expect(bars.every((bar) => Number.isFinite(bar.height))).toBe(true)
  })

  // AC row 34 (all values equal): every bar is full height, and none of them is NaN.
  it('gives every bar full height when all the values are equal', () => {
    const { bars, max } = barLayout(
      [
        { date: '2026-07-30', value: 42 },
        { date: '2026-08-01', value: 42 },
      ],
      '2026-07-30',
      '2026-08-02',
    )

    expect(max).toBe(42)
    expect(bars.map((bar) => bar.height)).toEqual([100, 100])
    expect(bars.map((bar) => bar.y)).toEqual([0, 0])
  })

  // AC row 34 (a single point): one bar, full height, finite — no `n - 1` divisor anywhere
  // that could turn a lone data point into a blank chart.
  it('draws a single point as one full-height bar', () => {
    const { bars, max } = barLayout([{ date: '2026-08-01', value: 7 }], '2026-07-30', '2026-08-02')

    expect(max).toBe(7)
    expect(bars).toHaveLength(1)
    expect(bars[0].height).toBe(100)
    expect(bars[0].y).toBe(0)
  })

  // AC row 34 (a 100x outlier): heights stay inside 0..100 — the outlier fills the frame
  // and the small day is a visible 1, not a negative or an overflowing bar.
  it('keeps every height inside 0..100 with a 100x outlier', () => {
    const { bars, max } = barLayout(
      [
        { date: '2026-07-30', value: 100 },
        { date: '2026-08-01', value: 10000 },
      ],
      '2026-07-30',
      '2026-08-02',
    )

    expect(max).toBe(10000)
    expect(bars.map((bar) => bar.height)).toEqual([1, 100])
    expect(bars.every((bar) => bar.height >= 0 && bar.height <= 100)).toBe(true)
    expect(bars.every((bar) => bar.y >= 0 && bar.y <= 100)).toBe(true)
    expect(bars.every((bar) => Number.isFinite(bar.height) && Number.isFinite(bar.y))).toBe(true)
  })

  // AC row 34 (nothing at all): no points is an empty layout with max 0, not a crash and
  // not a NaN-scaled phantom bar.
  it('returns no bars and a zero max for no points', () => {
    expect(barLayout([], '2026-07-30', '2026-08-02')).toEqual({ bars: [], max: 0 })
  })
})

describe('sparkline', () => {
  // AC row 34 (the points attribute, in the same 0..100 space): x is spread evenly across
  // n - 1 gaps and y is inverted because SVG measures y from the top. Pinned exactly,
  // because "it produced a string" is not an assertion.
  it('spreads points evenly and inverts y for SVG', () => {
    const line = sparkline([
      { date: '2026-07-30', value: 1 },
      { date: '2026-07-31', value: 2 },
      { date: '2026-08-01', value: 3 },
    ])

    expect(line).toEqual({ points: '0,100 50,50 100,0', min: 1, max: 3 })
  })

  // AC row 34 (a SINGLE point — the n - 1 divide-by-zero): one point would put x at
  // 0 / 0 = NaN and, with min === max, y at 0 / 0 = NaN too. A single point sits in the
  // middle of the frame at 50,50 and the string contains no NaN.
  it('places a single point at the centre instead of dividing by zero', () => {
    const line = sparkline([{ date: '2026-08-01', value: 7 }])

    expect(line).toEqual({ points: '50,50', min: 7, max: 7 })
    expect(line.points).not.toContain('NaN')
  })

  // AC row 34 (all values EQUAL — the other divide-by-zero, (value - min) / (max - min)):
  // a flat line renders down the middle, not as NaN.
  it('draws a flat mid-height line when every value is equal', () => {
    const line = sparkline([
      { date: '2026-07-30', value: 80 },
      { date: '2026-07-31', value: 80 },
      { date: '2026-08-01', value: 80 },
    ])

    expect(line).toEqual({ points: '0,50 50,50 100,50', min: 80, max: 80 })
    expect(line.points).not.toContain('NaN')
  })

  // AC row 34 (all values ZERO): min === max === 0 — both degenerate divisions at once.
  it('draws all-zero values as a flat line with no NaN', () => {
    const line = sparkline([
      { date: '2026-07-30', value: 0 },
      { date: '2026-08-01', value: 0 },
    ])

    expect(line).toEqual({ points: '0,50 100,50', min: 0, max: 0 })
    expect(line.points).not.toContain('NaN')
  })

  // AC row 34 (a 100x outlier): the range still maps onto 0..100 with nothing outside it
  // and nothing non-finite in the string.
  it('keeps a 100x outlier inside the frame', () => {
    const line = sparkline([
      { date: '2026-07-30', value: 100 },
      { date: '2026-08-01', value: 10000 },
    ])

    expect(line).toEqual({ points: '0,100 100,0', min: 100, max: 10000 })
    expect(line.points).not.toContain('NaN')
  })

  // AC row 34 (nothing at all): an empty series is an empty attribute, not 'NaN,NaN'.
  // `points=""` renders nothing on purpose; `points="NaN,NaN"` renders nothing by accident.
  it('returns an empty attribute for no points', () => {
    expect(sparkline([])).toEqual({ points: '', min: 0, max: 0 })
  })

  // AC row 34 (the blanket claim the row actually makes): across EVERY degenerate case,
  // no NaN appears anywhere in the returned attribute. Asserted as one sweep so a future
  // case added to the list is covered without a new test being remembered.
  it('never emits NaN in the points attribute for any degenerate input', () => {
    const cases = [
      [],
      [{ date: '2026-08-01', value: 0 }],
      [
        { date: '2026-07-31', value: 0 },
        { date: '2026-08-01', value: 0 },
      ],
      [
        { date: '2026-07-31', value: 5 },
        { date: '2026-08-01', value: 5 },
      ],
      [
        { date: '2026-07-31', value: 1 },
        { date: '2026-08-01', value: 100 },
      ],
    ]

    for (const points of cases) {
      expect(sparkline(points).points).not.toContain('NaN')
    }
  })
})
