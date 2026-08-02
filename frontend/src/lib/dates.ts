/**
 * Calendar arithmetic on 'YYYY-MM-DD' strings — the shape the API sends `entry_date` in.
 *
 * These moved out of `history.ts` (where `MONTHS` and `previousDay` were private) the
 * moment a second screen needed them. Shared, not copied: two implementations of "what is
 * the day before this one" is how two screens come to disagree about the 1st of the month.
 *
 * EVERYTHING HERE IS STRING-IN, STRING-OUT, AND NEVER READS THE CLOCK. A date is a label,
 * not an instant: `new Date('2026-08-01')` parses as UTC midnight and shifts a day in every
 * zone behind UTC, which is how a chart's axis ends up one column out of step with its own
 * bars. `Date.UTC` is used only as a calendar calculator — day 0 of a month is the last day
 * of the one before, so month and year boundaries fall out for free rather than needing a
 * table of month lengths that February breaks.
 */

/**
 * The profile fields a screen's request seam reads. Structural, so this module stays
 * react-free and a test can pass a bare object literal.
 */
export interface RequestProfile {
  timezone: string | null
}

/**
 * What a screen asks the API for: a window `days` wide, ending on the user's `today`.
 *
 * It lives here, in the leaf module, purely so `history.ts`, `trends.ts` and
 * `checkInView.ts` can all name the same shape without importing each other — `trends.ts`
 * already depends on `history.ts` for `localToday`, and the reverse import would close a
 * cycle. The shape is date-shaped anyway: a count of days and the day they end on.
 */
export interface ScreenRequest {
  days: number
  today: string
}

/** Abbreviated month names, indexed by month - 1. */
export const MONTHS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
]

/** 'YYYY-MM-DD' -> the calendar parts, as numbers. */
function parts(date: string): [number, number, number] {
  const [year, month, day] = date.split('-').map(Number)
  return [year, month, day]
}

/** A UTC-midnight instant used purely as a calendar calculator — never rendered. */
function utcDay(date: string): number {
  const [year, month, day] = parts(date)
  return Date.UTC(year, month - 1, day)
}

/** An instant from `utcDay` back to 'YYYY-MM-DD'. */
function toIsoDate(instant: number): string {
  return new Date(instant).toISOString().slice(0, 10)
}

/**
 * The day before `date`, both as 'YYYY-MM-DD'.
 *
 * Naive string math ('01' - 1) is the version that breaks on the first of the month.
 */
export function previousDay(date: string): string {
  const [year, month, day] = parts(date)
  return toIsoDate(Date.UTC(year, month - 1, day - 1))
}

/**
 * A date as a short axis label: '2026-08-01' -> 'Aug 1'.
 *
 * Deliberately NOT `toLocaleDateString`: its output varies with the runtime's locale, so an
 * assertion against it is not an assertion, and the label would differ between the test
 * runner, Toby's laptop and a user's phone. The day is unpadded — 'Aug 1', not 'Aug 01' —
 * because the chart's labels are prose-adjacent; the numbers that need to line up in
 * columns are the data, and those carry `tabular-nums`.
 */
export function monthDay(date: string): string {
  const [, month, day] = parts(date)
  return `${MONTHS[month - 1]} ${day}`
}

/**
 * Every day from `start` to `end`, INCLUSIVE of both ends, oldest first.
 *
 * This is the chart's axis, and it is built from the window the SERVER resolved
 * (`start_date`/`end_date` in the payload) rather than from anything the browser computes.
 * That is the whole of decision 2: the client never recomputes "today", so it can never
 * drift from the day the backend filed the data under — the exact class of bug #40 exists
 * to close and that this project has already shipped twice (#18, #39).
 *
 * Inclusive at both ends to match the backend's window exactly (`entry_date between $2 and
 * $3`, AC rows 1/2). If these two disagreed by a day, every bar would sit one column away
 * from its own label.
 */
export function enumerateDays(start: string, end: string): string[] {
  const days: string[] = []
  const last = utcDay(end)
  // Step with Date.UTC rather than by adding 86 400 000 to a timestamp: they agree here
  // (UTC has no DST), but only the calendar version stays correct if these ever become
  // zone-aware, and it is the same helper `previousDay` already trusts.
  for (let instant = utcDay(start); instant <= last; ) {
    const day = toIsoDate(instant)
    days.push(day)
    const [year, month, dayOfMonth] = parts(day)
    instant = Date.UTC(year, month - 1, dayOfMonth + 1)
  }
  return days
}
