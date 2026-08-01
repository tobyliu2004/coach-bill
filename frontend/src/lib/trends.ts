/**
 * The decisions and the geometry the Trends screen renders — extracted so they can be tested.
 *
 * Same split `checkInView.ts` and `history.ts` already established, for the same reason the
 * #18 and #39 retros made expensive: this project's vitest is node env over `src/**` with no
 * DOM, so a `.tsx` component cannot be mounted. Every bug those two tickets shipped was in a
 * DECISION — which state is this, whose timezone is this, is a failed load the same thing as
 * an empty one — and none of them were catchable inside a component.
 *
 * So this module answers "which state is this?", "where does this bar go?" and "is this
 * number a number?", and is tested. `Trends.tsx` answers "what does that look like?" and is
 * reviewed by eye. There is no arithmetic in the JSX.
 *
 * Nothing here reads the clock. `trendsRequest` takes the current instant as an argument,
 * exactly as `localToday` does — a function that reads the clock cannot be pinned to a
 * moment, and the one bug this module exists to prevent is a window computed from the wrong
 * reference point.
 */
import type { Trends } from './api'
import { enumerateDays, type RequestProfile, type ScreenRequest } from './dates'
import { localToday } from './history'

/**
 * The dashboard's window. 30 days, matching HISTORY_DAYS, so /trends and /history describe
 * the same span rather than two spans that merely look alike.
 *
 * Exported rather than inlined so a test can assert the screen asks for THIS number (#40):
 * a constant buried in a component is a constant nothing can pin.
 */
export const TRENDS_DAYS = 30

/**
 * What the Trends screen asks for: 30 days, ending on the USER's local today.
 *
 * THIS FUNCTION IS ISSUE #40. Before it existed, the same two decisions lived inline in the
 * screen — `localToday(profile?.timezone ?? null, new Date())` and a bare constant — where
 * nothing could assert them. Swapping the zone for `null` or the constant for `1` left all
 * 212 tests green while a guarantee this project has already shipped wrong twice (#18 in the
 * query, #39 on the label) silently died. Pulling both into one pure function makes the
 * wiring itself testable, which is the difference between a covered decision and a covered
 * screen.
 *
 * A missing profile or a missing timezone falls back to UTC — the same seatbelt the server's
 * `local_today` takes — rather than throwing mid-render.
 */
export function trendsRequest(profile: RequestProfile | null, now: Date): ScreenRequest {
  return { days: TRENDS_DAYS, today: localToday(profile?.timezone ?? null, now) }
}

/** Which state the trends screen is in. Mirrors `ListView`/`HistoryView` — same states. */
export type TrendsView =
  | { kind: 'loading' }
  /** The fetch FAILED. Never the empty state: "you have no data" when we simply couldn't
   *  ask reads as data loss — the bug the #18 reviewers caught, one screen further on. */
  | { kind: 'load-failed' }
  /** The fetch succeeded and the WINDOW genuinely holds nothing. Not "you have no data":
   *  we know this window is empty and know nothing about the year before it. */
  | { kind: 'empty' }
  | { kind: 'trends'; trends: Trends }

function isEmpty(trends: Trends): boolean {
  return (
    trends.volume.length === 0 &&
    trends.exercises.length === 0 &&
    trends.sleep.length === 0 &&
    trends.bodyweight.length === 0 &&
    trends.nutrition.length === 0
  )
}

export function trendsView(state: {
  loading: boolean
  loadFailed: boolean
  trends: Trends | null
}): TrendsView {
  if (state.loading) return { kind: 'loading' }
  // Before emptiness — an empty window means "nothing logged" ONLY if we actually got an
  // answer. Collapsing the two is how a failed fetch comes to read as data loss.
  if (state.loadFailed) return { kind: 'load-failed' }
  if (state.trends === null || isEmpty(state.trends)) return { kind: 'empty' }
  return { kind: 'trends', trends: state.trends }
}

/**
 * The deliberate string -> number parse `api.ts` has been promising since #19.
 *
 * Every measure crosses the wire as a Decimal-shaped STRING, because JSON's only number type
 * is a float and 61.235 kg has no business being rounded by a parser. The comment on
 * `WorkoutSet.weight_kg` says: "the day we do arithmetic in the browser, parse deliberately."
 * This is that day, and this is the one place it happens.
 *
 * Non-finite is null, never NaN. A NaN that reaches an SVG `d` or `points` attribute renders
 * NOTHING — a blank chart that looks exactly like "no data", which is the same
 * failure-reads-as-empty-state shape as `load-failed` vs `empty`, one layer down. Guarded
 * with `Number.isFinite` like `formatWeight` already is.
 *
 * `null` stays `null` and never becomes 0: on a bodyweight-only day `volume_kg` is null
 * because there was no tonnage to measure, not because the tonnage was zero. Those are
 * different facts and they draw different bars.
 */
export function parseNumeric(value: string | null | undefined): number | null {
  if (value === null || value === undefined) return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

/** One bar, in the 0..100 x 0..100 space the chart's viewBox uses. */
export interface Bar {
  date: string
  value: number
  x: number
  y: number
  width: number
  height: number
}

export interface BarLayout {
  bars: Bar[]
  /** The tallest value, so the screen can label the axis. 0 when there is nothing. */
  max: number
}

/** A point on any series, already parsed into a real number. */
export interface SeriesPoint {
  date: string
  value: number
}

/**
 * Lay a sparse series out as bars across the server's window.
 *
 * Coordinates are a fixed 0..100 x 0..100 space with y measured from the TOP, SVG's own
 * convention, so the component can hand this straight to a `viewBox="0 0 100 100"` and stay
 * a dumb renderer. Percent-shaped geometry also means the chart is resolution-independent
 * and the component never needs to measure the DOM.
 *
 * ONE BAR PER POINT, NOT PER DAY. A day with nothing logged is absent from the payload
 * (backend AC row 8) and stays absent here, so a gap renders as a gap. Padding the window
 * with zero-height bars would draw 28 claims that the user trained and lifted nothing —
 * `groupByDay`'s existing doctrine, one screen along: a rendered empty day is a lie, because
 * you didn't log nothing, you didn't log.
 *
 * The x position is the date's INDEX IN THE WINDOW, which is what makes the gap visible: pack
 * the points side by side instead and a month with two sessions renders as two adjacent bars
 * at the left edge, implying they happened on consecutive days.
 *
 * A point outside the window is dropped rather than drawn off-canvas — the axis comes from
 * the server (decision 2), so anything the server didn't put in this window has no place on it.
 */
export function barLayout(points: SeriesPoint[], start: string, end: string): BarLayout {
  const days = enumerateDays(start, end)
  const index = new Map(days.map((day, position) => [day, position]))
  const width = 100 / days.length

  const inWindow = points.filter((point) => index.has(point.date))
  // `Math.max(...[])` is -Infinity, which would poison every height. An empty series has a
  // max of 0 and no bars at all.
  const max = inWindow.length === 0 ? 0 : Math.max(...inWindow.map((point) => point.value))

  return {
    max,
    bars: inWindow.map((point) => {
      // The divide-by-zero guard, and the reason it is not paranoia: a day the user logged
      // with a value of 0 is real data, and every value being 0 makes `max` 0. Scaling would
      // be 0/0 = NaN, and a NaN height renders nothing — indistinguishable on screen from
      // "no data".
      const height = max > 0 ? (point.value / max) * 100 : 0
      return {
        date: point.date,
        value: point.value,
        x: (index.get(point.date) ?? 0) * width,
        y: 100 - height,
        width,
        height,
      }
    }),
  }
}

export interface Sparkline {
  /** An SVG `points` attribute: "x,y x,y ...". Empty string when there is nothing to draw. */
  points: string
  min: number
  max: number
}

/**
 * A series as a bare polyline, in the same 0..100 space.
 *
 * Scaled to its OWN min..max rather than to zero, because these are sleep hours, bodyweight
 * and calories: the interesting range is 6h-8h or 82kg-84kg, and a zero-based axis would
 * flatten every one of them into a straight line at the top of the frame. A sparkline is
 * read as shape, not as magnitude — the numbers themselves are printed next to it.
 *
 * Unlike `barLayout` this does NOT take the window: it spreads its points evenly rather than
 * placing them on a calendar. That is the conventional reading of a sparkline (shape over
 * time-scale) and it is why the volume chart — the one that has to show gaps honestly — is
 * the bar chart instead.
 *
 * Both degenerate divisions are guarded, and both are real rather than theoretical:
 *   * n === 1 makes the x step 100/(n-1) = 100/0. One point sits at the centre.
 *   * min === max makes the y scale (v-min)/(max-min) = 0/0. A flat series — every night
 *     exactly 8h, or a week of zeroes — draws down the middle.
 * Either one unguarded puts 'NaN' in the attribute, and an SVG polyline with a NaN in its
 * points renders nothing at all: a blank chart that reads as "no data" rather than as a bug.
 */
export function sparkline(points: SeriesPoint[]): Sparkline {
  if (points.length === 0) return { points: '', min: 0, max: 0 }

  const values = points.map((point) => point.value)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min
  const step = points.length === 1 ? 0 : 100 / (points.length - 1)

  return {
    min,
    max,
    points: points
      .map((point, position) => {
        const x = points.length === 1 ? 50 : position * step
        // A flat series has no range to scale against, so it sits mid-frame rather than
        // pinned to an arbitrary edge.
        const y = span === 0 ? 50 : 100 - ((point.value - min) / span) * 100
        return `${x},${y}`
      })
      .join(' '),
  }
}
