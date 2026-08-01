/**
 * Oracle suite for issue #20, PR 2 (Trends) — frontend row 30.
 *
 * Part of commit #1 on `feat/20-trends`, written BEFORE any implementation exists.
 * `./dates` does not exist: this file failing to resolve it is the CORRECT initial
 * failure, and no stub was created to make the import go green.
 *
 * Why calendar arithmetic gets its own module and its own oracle: the trends chart lays
 * its axis out across the SERVER's window (`start_date`..`end_date` from the payload —
 * decision 2, so the client never recomputes "today"), which means walking a date range
 * day by day in the browser. Naive string math on 'YYYY-MM-DD' breaks on the 1st of the
 * month, on the 1st of January, and on February — three off-by-ones that render as an
 * axis silently shifted under the bars.
 */
import { describe, expect, it } from 'vitest'
import { enumerateDays, monthDay } from './dates'

describe('enumerateDays', () => {
  // AC row 30: enumerateDays('2026-07-30','2026-08-02') -> 4 days INCLUSIVE of both ends,
  // crossing the month boundary. The exact list, not the length alone: a helper that
  // produced four dates none of which were Aug 1 would satisfy a count assertion.
  it('lists every day inclusive of both ends, across a month boundary', () => {
    expect(enumerateDays('2026-07-30', '2026-08-02')).toEqual([
      '2026-07-30',
      '2026-07-31',
      '2026-08-01',
      '2026-08-02',
    ])
  })

  // AC row 30 (the count, asserted on its own): four days, not three (exclusive end) and
  // not five (a fencepost). The window's length is what every bar's width is divided by,
  // so an off-by-one here is an axis that does not line up with its own bars.
  it('is inclusive at both ends, so a 4-day window has 4 days', () => {
    expect(enumerateDays('2026-07-30', '2026-08-02')).toHaveLength(4)
  })

  // AC row 30 (the degenerate window): start == end is one day, not zero and not two.
  it('returns a single day when start and end are the same date', () => {
    expect(enumerateDays('2026-08-01', '2026-08-01')).toEqual(['2026-08-01'])
  })

  // AC row 30 (the year boundary — the same bug one notch harder): December rolls into
  // January and the year increments with it.
  it('crosses a year boundary', () => {
    expect(enumerateDays('2025-12-30', '2026-01-02')).toEqual([
      '2025-12-30',
      '2025-12-31',
      '2026-01-01',
      '2026-01-02',
    ])
  })

  // AC row 30 (variable month lengths): January has 31 days and February 28 in 2026.
  // A helper that assumed 30-day months would emit a '2026-01-32' or skip Feb 1.
  it('respects the actual length of the month it is leaving', () => {
    expect(enumerateDays('2026-01-30', '2026-02-02')).toEqual([
      '2026-01-30',
      '2026-01-31',
      '2026-02-01',
      '2026-02-02',
    ])
  })

  // AC row 30 (a real default window): the 30-day span the dashboard asks for has exactly
  // 30 entries — the same inclusive reading the backend's window uses (backend rows 1/2),
  // so the axis and the query can never disagree about how wide "30 days" is.
  it('spans exactly 30 days for the dashboard default window', () => {
    const days = enumerateDays('2026-07-03', '2026-08-01')

    expect(days).toHaveLength(30)
    expect(days[0]).toBe('2026-07-03')
    expect(days[29]).toBe('2026-08-01')
  })
})

describe('monthDay', () => {
  // AC row 30 (the axis LABEL half). NOTE FOR THE GATE: the approved table says "the axis"
  // and pins the day math; it does not name a label format. 'Aug 1' is the format the
  // agreed API surface for `lib/dates.ts` spells out, pinned here as a literal because
  // `toLocaleDateString` varies with the runner's locale and an unpinned format is not an
  // assertion. If a different format is wanted, that is a correctness-table edit.
  it('renders a date as an abbreviated month and an unpadded day', () => {
    expect(monthDay('2026-08-01')).toBe('Aug 1')
  })

  it('renders a two-digit day without padding it differently', () => {
    expect(monthDay('2026-12-31')).toBe('Dec 31')
  })

  // AC row 30: the first month of the year is 'Jan', not the zero-indexed month before it.
  // An off-by-one in the month table labels January as December.
  it('does not off-by-one the month name', () => {
    expect(monthDay('2026-01-15')).toBe('Jan 15')
  })
})
