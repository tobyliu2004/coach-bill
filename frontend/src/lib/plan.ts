/**
 * Every decision the /plan and /diet screens make (issue #51).
 *
 * WHY THIS IS A `lib/` MODULE AND NOT LOGIC INSIDE A `.tsx`. vitest here is node-env over
 * `src/**` with no DOM, so a component cannot be mounted — the #18/#39/#40 lesson, paid for
 * three times. So "WHICH state is this?" lives here and is exhaustively tested; "what does
 * that state look like?" lives in the pages and is reviewed by eye. Every bug those retros
 * shipped was in the first question.
 *
 * Nothing here reaches for a clock, a fetch or `Intl`'s default zone. `now` and the profile
 * timezone are arguments, which is the whole of why the timezone rule is testable at all.
 */

import type { Plan, TrendsNutritionPoint } from './api'
import { localToday } from './history'
import { parseNumeric } from './trends'

// =======================================================================================
// The request seams (#40's shape, #39's rule)
// =======================================================================================
//
// A window and a timezone that live inline in a screen are a window and a timezone no test
// can pin: #40 proved it by swapping the zone for `null` and watching 212 tests stay green.
// These exist so the call sites are assertable.

/** The profile fields these decisions read (structural, so this module stays react-free). */
export interface ProfileZone {
  timezone: string | null
}

/**
 * What /plan needs to know before it renders: which day is today, for the USER.
 *
 * `design.md`: every date the user reads is rendered from `profiles.timezone`, never the
 * browser's. A missing zone falls back to UTC — the same seatbelt the server's `local_today`
 * takes — rather than throwing mid-render.
 */
export function planRequest(profile: ProfileZone | null, now: Date): { today: string } {
  return { today: localToday(profile?.timezone ?? null, now) }
}

/**
 * What /diet needs: today's food, and only today's.
 *
 * `days: 1` is sent explicitly rather than left off the URL. The two are equivalent to the
 * server, but a window the screen never names is a window no test can pin — #40 again.
 */
export function dietRequest(
  profile: ProfileZone | null,
  now: Date,
): { days: number; today: string } {
  return { days: 1, today: localToday(profile?.timezone ?? null, now) }
}

// =======================================================================================
// /plan — five states, and they must all stay distinct (rows 22, 23, 24)
// =======================================================================================

/**
 * Which state the plan screen is in.
 *
 * ⚠️ FIVE MEMBERS, ALL DISTINCT, AND THAT IS THE POINT RATHER THAN A DETAIL.
 * Issue #43 is a collapse: `loading` rendering as nothing reads as "you have no plan", and
 * a failed load rendering as empty reads as data loss. Here a fetch in flight, a failed
 * fetch, a generation in flight, an answered fetch with no plan, and a plan are five
 * different claims about the world and get five different `kind`s.
 *
 * EVERY member carries `canGenerate`, because the page renders one submit button in every
 * state and row 24's "the button cannot be double-fired" is about that button. Putting the
 * flag only on the states that happen to show it today would mean the page has to narrow
 * before it can ask — and would let a new state ship without an answer.
 */
export type PlanView = { canGenerate: boolean } & (
  | { kind: 'loading' }
  /** The load failed. `retry: true` is the affordance row 23 requires — an error the user
   *  can act on, never a silent "you have no plan". */
  | { kind: 'load-failed'; retry: true }
  /** A generation is in flight: "Bill is writing your plan…". */
  | { kind: 'generating' }
  /** We asked, we got an answer, and the answer is that there is no plan yet. */
  | { kind: 'empty' }
  | { kind: 'plan'; plan: Plan }
)

/**
 * The plan screen's state, from the four things the page knows.
 *
 * ORDER IS LOAD-BEARING, and it is the same order `listView` uses for the same reason:
 * `generating` first because it is the state the user just caused and it outranks a
 * background refetch; then `loading`; then `loadFailed` BEFORE emptiness, because "no plan"
 * only means "nothing yet" if we actually got an answer.
 */
export function planView(state: {
  loading: boolean
  loadFailed: boolean
  generating: boolean
  plan: Plan | null
}): PlanView {
  // While a plan is being written, the button is withheld — a second dispatch is impossible
  // from the view state itself, rather than guarded inside a handler no node-env test could
  // see. Every other state offers it: a flag that can never be true is not a flag.
  if (state.generating) return { kind: 'generating', canGenerate: false }
  // Also false while the FIRST fetch is in flight, and this is about the label, not the
  // spinner. In `loading` the page renders the button as "Write my plan" — the
  // `kind === 'plan'` branch that would say "Write a NEW plan" and warn "this replaces the
  // plan above" has not been reached yet, because we do not know yet that a plan exists.
  // Clicking in that window spends a Sonnet call AND archives a plan the user was never
  // told they had. Disabling for the sub-second fetch costs nothing and removes the only
  // state where the button misdescribes what it does.
  if (state.loading) return { kind: 'loading', canGenerate: false }
  if (state.loadFailed) return { kind: 'load-failed', retry: true, canGenerate: true }
  if (state.plan === null) return { kind: 'empty', canGenerate: true }
  return { kind: 'plan', plan: state.plan, canGenerate: true }
}

// =======================================================================================
// /diet — target and actual, as two numbers (rows 18, 19, 20, 21)
// =======================================================================================

/** One day's macros, already parsed. The JSX does no arithmetic and no `Number(...)`. */
export interface DietMeasures {
  calories: number | null
  protein_g: number | null
  carbs_g: number | null
  fat_g: number | null
}

/**
 * What /diet shows.
 *
 * ⚠️ TWO MEMBERS BECAUSE ROWS 19 AND 21 ARE DIFFERENT CLAIMS ABOUT THE WORLD, and the whole
 * of the null-vs-zero doctrine lives in the difference:
 *
 *   no active plan          -> 'no-target'. There is NO `target` key at all. We do not have
 *                             a target, and 0 is not a way of saying that (row 19).
 *   plan, nothing logged    -> 'target', consumed all zeros. Zero is the honest answer: we
 *                             know they logged nothing today, which is a fact (row 21).
 *
 * Collapsing them is the bug that shipped as "peak 0 lb" on /trends — the feature's own
 * doctrine undone in the presentation layer.
 *
 * And NO PERCENTAGE, anywhere, under any name (row 18). 1800 against 2400 is two numbers.
 */
export type DietView =
  | { kind: 'no-target'; consumed: DietMeasures }
  | { kind: 'target'; consumed: DietMeasures; target: DietMeasures }

/**
 * Today's food out of the sparse `/trends` series — row 20, re-pointed by amendment 3.
 * https://github.com/tobyliu2004/coach-bill/issues/51#issuecomment-5389340006
 *
 * ⚠️ THE CLIENT NEVER SUMS. `GET /trends` returns ONE POINT PER DAY, already summed by
 * Postgres — three meals across three check-ins arrive as one point. That is row 20's
 * "summed, not latest-wins", and it happens in SQL where it can be graded against a real
 * database. There is nothing here to add up, and adding anything up would be a second
 * opinion about what the user ate.
 *
 * Matched BY DATE, never by position. The series is oldest-first, but "the last element" is
 * only today's point when today happens to be the end of the window — and on /diet, a user
 * who logged nothing today has no point at all, so the last element would be YESTERDAY'S
 * food reported as today's.
 *
 * ABSENT -> null, never a zero-filled object. The series is sparse: a day with nothing
 * logged is missing, not a row of zeros. `dietView` turns that null into a rendered 0
 * against the target — but that is layer 2's decision, and conflating the two here would
 * make rows 19 and 21 indistinguishable one level below where anything else looks.
 */
export function todaysIntake(
  nutrition: TrendsNutritionPoint[],
  today: string,
): Omit<TrendsNutritionPoint, 'date'> | null {
  const point = nutrition.find((entry) => entry.date === today)
  if (point === undefined) return null
  return {
    calories: point.calories,
    protein_g: point.protein_g,
    carbs_g: point.carbs_g,
    fat_g: point.fat_g,
  }
}

/** Every measure of one wire object, parsed once. */
function measures(source: {
  calories: string
  protein_g: string
  carbs_g: string
  fat_g: string
}): DietMeasures {
  return {
    calories: parseNumeric(source.calories),
    protein_g: parseNumeric(source.protein_g),
    carbs_g: parseNumeric(source.carbs_g),
    fat_g: parseNumeric(source.fat_g),
  }
}

const NOTHING_LOGGED: DietMeasures = { calories: 0, protein_g: 0, carbs_g: 0, fat_g: 0 }

/**
 * The diet screen's state: what they ate, and what they were aiming at.
 *
 * `consumed: null` means "nothing logged today", which `todaysIntake` established from the
 * sparse series. It becomes 0 here — a real number the user can read — and never a null
 * that renders as blank, because a blank is indistinguishable from a screen that failed.
 *
 * `parseNumeric` returns null for anything that is not a finite number, so a malformed
 * target reads as "no target for this macro" rather than as a 0 kcal target. That is the
 * same null-vs-zero rule, one level down.
 */
export function dietView(state: {
  plan: Plan | null
  consumed: { calories: string; protein_g: string; carbs_g: string; fat_g: string } | null
}): DietView {
  const consumed = state.consumed === null ? NOTHING_LOGGED : measures(state.consumed)

  if (state.plan === null) return { kind: 'no-target', consumed }

  return {
    kind: 'target',
    consumed,
    target: measures({
      calories: state.plan.calories_target,
      protein_g: state.plan.protein_g_target,
      carbs_g: state.plan.carbs_g_target,
      fat_g: state.plan.fat_g_target,
    }),
  }
}
