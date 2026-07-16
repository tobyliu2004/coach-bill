/**
 * The decisions AppHome renders — extracted so they can be tested.
 *
 * These are pure functions over data, not markup. That split exists for a reason the #18
 * retro made expensive: every bug the reviewers caught on that ticket was a frontend error
 * state (a failed load rendering as "no check-ins yet"; an expired session not signing the
 * user out), and none of them were catchable because the logic lived inside a `.tsx`
 * component that this project's vitest setup (node env, `src/**` only, no DOM) can't mount.
 *
 * So: the question "WHICH state is this?" lives here and is tested. The question "what does
 * that state look like?" lives in AppHome and is reviewed by eye. The bugs were all in the
 * first question.
 */
import { ApiAuthError, type CheckIn } from './api'

/** Which block belongs under one check-in's text. */
export type FactsView =
  /** Extraction broke. Must be visibly distinct from `none` — a failure that renders as an
   *  empty state looks like data loss, which is exactly #18's bug. */
  | { kind: 'failed' }
  /** Extraction ran and found nothing. Success: render the text, no block, NO error. */
  | { kind: 'none' }
  /** Extraction found facts. `partial` rides along: some facts landed, but one item didn't
   *  read, and the user is told so rather than silently shown a short list. */
  | { kind: 'facts'; facts: CheckIn['facts']; partial: boolean }

/** What to do when a call throws. */
export type ErrorAction =
  /** The session is gone. Sign out — anything else strands the user on a screen that can
   *  never succeed. */
  | { kind: 'sign-out' }
  /** Everything else is transient: say so in-app and let them retry. */
  | { kind: 'message' }

/** Which state the check-in list is in. */
export type ListView =
  | { kind: 'loading' }
  /** The fetch FAILED. Never the empty state: "you have no check-ins" when we simply
   *  couldn't ask reads as data loss. */
  | { kind: 'load-failed' }
  /** The fetch succeeded and there is genuinely nothing yet. */
  | { kind: 'empty' }
  | { kind: 'list'; checkIns: CheckIn[] }

function hasFacts(facts: CheckIn['facts']): boolean {
  return (
    facts.sets.length > 0 ||
    facts.nutrition.length > 0 ||
    facts.sleep.length > 0 ||
    facts.bodyweight.length > 0
  )
}

export function factsView(checkIn: CheckIn): FactsView {
  // Status first, facts second. A 'failed' check-in has no facts by definition, so deciding
  // on emptiness first would collapse it into `none` — the bug this function exists to
  // prevent.
  if (checkIn.extraction_status === 'failed') return { kind: 'failed' }
  if (!hasFacts(checkIn.facts)) return { kind: 'none' }
  return { kind: 'facts', facts: checkIn.facts, partial: checkIn.extraction_status === 'partial' }
}

export function errorAction(error: unknown): ErrorAction {
  // Only an auth failure signs the user out. Doing it on a 500 would be its own bug: a
  // vendor blip would look like being logged out.
  return error instanceof ApiAuthError ? { kind: 'sign-out' } : { kind: 'message' }
}

export function listView(state: {
  loading: boolean
  loadFailed: boolean
  checkIns: CheckIn[]
}): ListView {
  if (state.loading) return { kind: 'loading' }
  // Before emptiness — an empty list means "nothing yet" ONLY if we actually got an answer.
  if (state.loadFailed) return { kind: 'load-failed' }
  if (state.checkIns.length === 0) return { kind: 'empty' }
  return { kind: 'list', checkIns: state.checkIns }
}
