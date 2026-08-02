/**
 * The decisions the History screen renders — extracted so they can be tested.
 *
 * Same split as `checkInView.ts`, for the same reason the #18 retro made expensive: this
 * project's vitest is node-env over `src/**` with no DOM, so a `.tsx` component cannot be
 * mounted and anything living inside one is reviewed by eye rather than asserted. So the
 * questions "which state is this?", "which day does this row belong to?" and "what do we
 * call that day?" live here and are tested; only "what does that look like?" lives in the
 * component.
 *
 * Nothing here reads the clock. `localToday` takes the current instant as an argument
 * instead of calling `new Date()` itself — a function that reads the clock cannot be pinned
 * to a moment, and the one bug this module exists to prevent is a date computed from the
 * wrong reference point.
 */
import type { CheckIn } from './api'
import { MONTHS, previousDay, type RequestProfile, type ScreenRequest } from './dates'

/** One day's check-ins, in the order the API returned them. */
export interface DayGroup {
  /** The `entry_date` this group is keyed by, as the API sends it: 'YYYY-MM-DD'. */
  date: string
  checkIns: CheckIn[]
}

/** Which state the history screen is in. Mirrors `ListView` — same states, grouped payload. */
export type HistoryView =
  | { kind: 'loading' }
  /** The fetch FAILED. Never the empty state: "you have no check-ins" when we simply
   *  couldn't ask reads as data loss — the exact bug the #18 reviewers caught. */
  | { kind: 'load-failed' }
  /** The fetch succeeded and the window genuinely holds nothing. */
  | { kind: 'empty' }
  | { kind: 'days'; days: DayGroup[] }

/**
 * The History screen's window: the last 30 days.
 *
 * Exported rather than inlined in the screen so a test can assert the screen asks for THIS
 * number — see `historyRequest` below and issue #40.
 */
export const HISTORY_DAYS = 30

/**
 * Today's date in the user's own zone, as 'YYYY-MM-DD' — the same string shape the API
 * sends for `entry_date`, so the two can be compared directly without either becoming a
 * `Date`.
 *
 * This is the browser half of `local_today` on the server. Using the browser's own zone
 * instead would reintroduce #18's timezone bug on the label: an LA user checking in at
 * 17:30 has an `entry_date` of Aug 1, but a traveller's laptop set to UTC would already
 * call it Aug 2 and label their check-in "Yesterday". `profiles.timezone` is the one
 * answer both sides use.
 *
 * `now` is a parameter, not `new Date()`, so the caller owns the reference instant and the
 * function is pinnable in a test.
 */
export function localToday(timezone: string | null, now: Date): string {
  const parts = formatParts(timezone ?? 'UTC', now)
  return `${parts.year}-${parts.month}-${parts.day}`
}

function formatParts(
  timeZone: string,
  now: Date,
): { year: string; month: string; day: string } {
  let parts: Intl.DateTimeFormatPart[]
  try {
    parts = new Intl.DateTimeFormat('en-US', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).formatToParts(now)
  } catch {
    // An IANA zone this browser doesn't know throws RangeError. The value is validated
    // against zoneinfo when it's written (schemas/profiles.py), so this is a seatbelt for a
    // stale browser, not an expected path — but it is one that would white-screen the app
    // mid-render, and the server takes the identical UTC fallback for a missing zone.
    parts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'UTC',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).formatToParts(now)
  }
  const value = (type: Intl.DateTimeFormatPartTypes): string =>
    parts.find((part) => part.type === type)?.value ?? ''
  return { year: value('year'), month: value('month'), day: value('day') }
}

/**
 * What the History screen asks for: 30 days, ending on the USER's local today.
 *
 * THIS FUNCTION IS ISSUE #40, the /history third of it. These two decisions used to live
 * inline in the screen — `localToday(profile?.timezone ?? null, new Date())` next to a bare
 * `HISTORY_DAYS` — where nothing could assert them: swapping the zone for `null` or the
 * constant for `1` left all 212 tests green while the timezone guarantee silently died.
 * Rows 21-25 of PR 1 closed the *decision* dimension; this closes the *wiring* one.
 *
 * A missing profile or timezone falls back to UTC — the same seatbelt the server's
 * `local_today` takes — rather than throwing mid-render.
 */
export function historyRequest(profile: RequestProfile | null, now: Date): ScreenRequest {
  return { days: HISTORY_DAYS, today: localToday(profile?.timezone ?? null, now) }
}

/**
 * What to call a day: 'Today', 'Yesterday', or the date.
 *
 * `today` is passed in (from `localToday`) rather than read here, because "today" is the
 * user's, not the browser's. The year is carried only when it differs from the current one —
 * "Dec 31" eight months into the next year is ambiguous, and a year on every row is noise.
 */
export function dayLabel(entryDate: string, today: string): string {
  if (entryDate === today) return 'Today'
  if (entryDate === previousDay(today)) return 'Yesterday'

  const [year, month, day] = entryDate.split('-')
  const label = `${MONTHS[Number(month) - 1]} ${Number(day)}`
  return year === today.slice(0, 4) ? label : `${label}, ${year}`
}

/**
 * Collapse a flat, API-ordered list into one group per day.
 *
 * Built from the check-ins themselves rather than by walking the calendar across the
 * window, which is what makes "days with no check-ins are omitted entirely" true by
 * construction rather than by a filter someone could remove. A rendered empty day would be
 * a lie: it says you logged nothing, when the truth is you didn't log.
 *
 * Order is inherited, not recomputed. The API already returns newest `entry_date` first and
 * newest `created_at` first within a day (backend AC row 10), and a Map keyed by date
 * preserves first-appearance order — so re-sorting here would be a second, silently
 * divergent opinion about ordering rather than a safety net.
 */
export function groupByDay(checkIns: CheckIn[]): DayGroup[] {
  const groups: DayGroup[] = []
  const byDate = new Map<string, DayGroup>()

  for (const checkIn of checkIns) {
    let group = byDate.get(checkIn.entry_date)
    if (group === undefined) {
      group = { date: checkIn.entry_date, checkIns: [] }
      byDate.set(checkIn.entry_date, group)
      groups.push(group)
    }
    group.checkIns.push(checkIn)
  }
  return groups
}

export function historyView(state: {
  loading: boolean
  loadFailed: boolean
  checkIns: CheckIn[]
}): HistoryView {
  if (state.loading) return { kind: 'loading' }
  // Before emptiness — an empty window means "nothing logged" ONLY if we actually got an
  // answer. Collapsing the two is how a failed fetch comes to read as data loss.
  if (state.loadFailed) return { kind: 'load-failed' }
  if (state.checkIns.length === 0) return { kind: 'empty' }
  return { kind: 'days', days: groupByDay(state.checkIns) }
}
