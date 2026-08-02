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

/**
 * A byte-for-byte mirror of `OFF_TOPIC_REPLY` in backend/app/ai/coach.py.
 *
 * Duplicated, which normally would not be acceptable — but the alternative was worse. The
 * screen needs to know whether a stored reply is the off-topic constant (only those can be
 * retracted and re-asked; a crisis reply never can, by design). Putting a `retractable`
 * flag on the wire was the obvious move and is not available: AC row 1 pins the response
 * body to EXACTLY `{id, content, created_at}`, and the oracle asserts that set.
 *
 * The drift risk is handled by a test rather than by hope:
 * `backend/tests/test_coach_copy_parity.py` reads THIS file and fails if the two strings
 * stop matching. So editing the backend constant without editing this one turns the suite
 * red — which is the property that makes a duplicated string safe to live with.
 */
export const OFF_TOPIC_REPLY = `I only read training, food, sleep and bodyweight check-ins — that one's outside my lane.

Try something like "bench 135 4×8, slept 6h, knee felt tweaky" and I'll have something useful to say.`

/**
 * Can the user ask Bill again about this reply?
 *
 * ONLY for the off-topic constant, and that is a safety rule rather than a product one. If
 * any reply could be re-asked, someone in genuine crisis could re-roll past the hotlines
 * until the gate handed them coaching instead — the app would be helping them get away from
 * its own safety response. A real coach reply is excluded too, more prosaically: "give me a
 * different answer" is a request to spend money again, which belongs behind #26's caps.
 *
 * The server enforces this independently (`services/coach.py::retract_off_topic_reply`
 * matches on content in the same statement as the delete). This function only decides
 * whether to SHOW the affordance — a client that called the endpoint anyway would get a 404.
 */
export function isRetractable(content: string): boolean {
  return content === OFF_TOPIC_REPLY
}

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
