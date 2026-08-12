/**
 * The decisions `CoachReply` renders — extracted so they can be tested.
 *
 * Pure functions over data, not markup, for the reason the #18 retro made expensive: every
 * frontend bug that ticket shipped was an error state (a failed load rendering as "you have
 * no check-ins"), and none were catchable because the logic lived inside a `.tsx` that this
 * project's vitest (node env, `src/**`, no DOM) cannot mount. So "WHICH state is this?"
 * lives here and is tested; "what does that state look like?" lives in the component and is
 * reviewed by eye. The bugs were all in the first question.
 *
 * Same split as `checkInView.ts::listView`.
 */
import type { CheckIn } from './api'

/*
 * #48 REMOVED A MIRRORED BACKEND CONSTANT FROM THIS FILE, AND THE REASON IS WORTH KEEPING.
 *
 * There used to be an off-topic reply constant here — a byte-for-byte copy of a backend
 * string — plus an `isRetractable` that compared a stored reply against it, so the screen
 * could offer
 * "Ask again" on a reply the gate had mislabelled. The duplication was defensible only
 * because a backend test read this file and failed if the two strings drifted.
 *
 * The gate no longer has an off-topic label (Bill handles scope himself, in prose), so
 * there is no constant to mirror, nothing to retract, and no parity test to maintain. The
 * screen went back to rendering `reply.content` and nothing else.
 *
 * The lesson, since the next feature will be tempted the same way: the client wanted to know
 * something ABOUT a reply that the wire format did not carry, and copying a string across
 * the boundary was the cheapest way to fake it. If that comes up again, the honest fixes are
 * a field on the response or a decision the server makes — not a second copy of the truth.
 */

/** Which block belongs under one check-in, where Bill's reply goes. */
export type ReplyView =
  /** No reply, none asked for. Renders NOTHING — not an empty box, not a placeholder.
   *  Most history cards are this. */
  | { kind: 'none' }
  /** A request is in flight. Renders "Bill is reading your check-in…" — a 3-6s blank space
   *  reads as "it's broken", which is #43's bug class and not one to ship twice. */
  | { kind: 'thinking' }
  /** The request failed. Renders a muted message AND a retry affordance — never silence,
   *  and never the same pixels as `none`. A failure that looks like an empty state is
   *  indistinguishable from "Bill had nothing to say", which is a lie. */
  | { kind: 'failed'; announce: boolean }
  /** A reply to render. `content` is the stored text, rendered as plain text. */
  | { kind: 'reply'; content: string; announce: boolean }

/**
 * What the reply block should be right now.
 *
 * `live` is the parameter that matters and the one that is easy to miss. The SAME component
 * renders on `/app` — where a reply arriving after you submit should interrupt a screen
 * reader, because you are waiting for it — and on `/history`, where thirty cards mount at
 * once and thirty announcements would be a barrage. That is the trap open as issue #41,
 * and making it an input here is what moves the decision somewhere a test can reach it:
 * `announce` is asserted as a COUNT over 30 mounted history cards, because a single-card
 * assertion would pass an implementation that announces every one.
 *
 * Order is deliberate. An existing reply wins over everything, so a retry that already
 * succeeded never flickers back to "thinking"; `requesting` beats `failed`, so pressing
 * retry replaces the error with progress instead of showing both.
 */
export function replyView(state: {
  reply: CheckIn['reply']
  requesting: boolean
  failed: boolean
  live: boolean
}): ReplyView {
  if (state.reply) {
    return { kind: 'reply', content: state.reply.content, announce: state.live }
  }
  if (state.requesting) return { kind: 'thinking' }
  // Before `none` — "Bill hasn't answered" is only true if we actually got an answer.
  if (state.failed) return { kind: 'failed', announce: state.live }
  return { kind: 'none' }
}
