import type { CheckIn } from '../lib/api'
import { isRetractable, replyView } from '../lib/coachView'

/**
 * Coach Bill's reply under a check-in — the thing the app is named after.
 *
 * Which state this is comes from `replyView` (tested); this file only renders it. The four
 * states must stay visibly distinct, and two of them are the ones that go wrong:
 * `thinking` vs nothing (a 3-6s blank space reads as broken — #43), and `failed` vs `none`
 * (a failure that renders as an empty state is indistinguishable from data loss — the #18
 * retro's headline finding).
 *
 * Rendering notes (design.md): the card already spent `bg-surface`, so this separates with a
 * `border-edge` divider INSIDE it rather than stepping up to `bg-raised` (spec'd for
 * popovers) — the same treatment `Facts.tsx` uses, because they are siblings under the same
 * card. Amber is spent on the two-character `Bill ·` prefix and nothing else, which keeps
 * the accent well under 5% of the screen while still marking whose voice this is. Nothing
 * animates: the daily app is a repeated-action surface.
 *
 * Bill's prose is the ONE place in the app that is `font-sans` rather than `font-mono` —
 * everything else here is data (numbers, timestamps, logged text) and mono is the rule for
 * data. This is writing, and setting it in mono would make a coach's sentences read like a
 * terminal dump. Same treatment as the landing page's `CheckInChapter`.
 */

/** One quiet inline action. Shared so "Try again", "Ask Bill" and "Ask again" cannot drift
 *  apart visually — they are the same affordance in three different states. */
function ReplyAction({
  onClick,
  disabled = false,
  children,
}: {
  onClick: () => void
  /** True while a request for this check-in is already in flight. DISABLING THIS IS A COST
   *  CONTROL, not a polish detail: every click reaches the model, and only the first one's
   *  reply gets stored (the rest lose the unique-index race), so a double-click is a
   *  double bill for one visible answer. */
  disabled?: boolean
  children: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="rounded-control font-mono text-xs text-fg underline underline-offset-4 transition-colors duration-150 hover:text-accent focus:text-accent focus:outline-none disabled:no-underline disabled:opacity-50"
    >
      {children}
    </button>
  )
}

export function CoachReply({
  checkIn,
  requesting,
  failed,
  live,
  onRequest,
  onRetract,
}: {
  checkIn: CheckIn
  /** A reply request is in flight for THIS check-in. */
  requesting: boolean
  /** The last request for THIS check-in failed. */
  failed: boolean
  /** True on the Today screen, false on History. Drives whether a screen reader is
   *  interrupted — 30 history cards must not fire 30 announcements (#41) — and whether the
   *  ask/retry affordances appear at all. History is read-only. */
  live: boolean
  /** Ask Bill to reply to this check-in. Used by both `failed` and `none`. */
  onRequest: () => void
  /** Throw away an off-topic reply and ask again. Only reachable when `isRetractable`. */
  onRetract: () => void
}) {
  const view = replyView({ reply: checkIn.reply, requesting, failed, live })

  if (view.kind === 'none') {
    // Read-only screens render nothing here, which is AC row 40.
    if (!live) return null
    // On Today, "no reply" needs a way OUT of that state. Before this existed, a reply that
    // failed and was then reloaded past became unreachable forever: `failed` lives in
    // component state, so after a refresh this is `none` and the retry button was gone —
    // the #18-retro bug class re-appearing one boundary over, across a reload rather than
    // within a render. It also covers every check-in logged before this feature shipped.
    // Deliberately NOT auto-requested on mount: that would spend a Sonnet call on every
    // page load for every replyless check-in.
    return (
      <p className="mt-3 border-t border-edge pt-3 font-mono text-xs text-fg-muted">
        <ReplyAction onClick={onRequest} disabled={requesting}>
          Ask Bill
        </ReplyAction>
      </p>
    )
  }

  if (view.kind === 'thinking') {
    // Not a spinner: a sentence that says what is happening. The wait is long enough that
    // "something is happening" is less reassuring than "Bill is reading YOUR check-in".
    // No `role="status"` — announcing the wait itself would interrupt a screen-reader user
    // twice for one reply, and the reply is the part worth interrupting for.
    return (
      <p className="mt-3 border-t border-edge pt-3 font-mono text-xs text-fg-muted">
        Bill is reading your check-in…
      </p>
    )
  }

  if (view.kind === 'failed') {
    return (
      <div
        className="mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-edge pt-3"
        // Only the live screen announces. On History this is a plain region, so a window
        // where every reply failed doesn't read 30 errors aloud on mount (#41).
        {...(view.announce ? { role: 'alert' } : {})}
      >
        <p className="font-mono text-xs text-fg-muted">
          Bill couldn’t answer that one. Your check-in is saved.
        </p>
        {/* A retry affordance, not just a message. The failure is transient by
            construction — the server stored nothing — so "try again" is a real action and
            the user should not have to reload the page to take it. */}
        <ReplyAction onClick={onRequest} disabled={requesting}>
          Try again
        </ReplyAction>
      </div>
    )
  }

  return (
    <div
      className="mt-3 border-t border-edge pt-3"
      // Announced on /app, where the user submitted and is waiting for exactly this.
      // Silent on /history, where it is one of thirty cards that mounted at once.
      {...(view.announce ? { role: 'status', 'aria-live': 'polite' } : {})}
    >
      <p className="text-sm leading-relaxed text-fg">
        <span className="font-mono text-accent">Bill ·</span>{' '}
        {/* Interpolated as TEXT, never `dangerouslySetInnerHTML`. Model output is untrusted
            content going onto a page; React escapes this, and that is the whole defence.
            `whitespace-pre-wrap` keeps Bill's paragraph breaks (the crisis reply has them)
            and `break-words` stops a long unbroken string from widening the card. */}
        <span className="font-sans whitespace-pre-wrap break-words">{view.content}</span>
      </p>
      {/* ONLY for the off-topic constant, and only on the live screen. The gate is a model
          and will sometimes call a real training check-in off-topic; without this, that
          verdict is permanent because the endpoint is get-or-create.

          A crisis reply is never retractable — `isRetractable` says so and the server
          enforces it independently. If it were, someone in genuine crisis could ask again
          until the gate handed them coaching instead of the hotlines. */}
      {live && isRetractable(view.content) && (
        <p className="mt-2 font-mono text-xs text-fg-muted">
          Actually about your training?{' '}
          <ReplyAction onClick={onRetract} disabled={requesting}>
            Ask again
          </ReplyAction>
        </p>
      )}
    </div>
  )
}
