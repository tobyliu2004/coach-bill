import type { CheckIn } from '../lib/api'
import { replyView } from '../lib/coachView'

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
export function CoachReply({
  checkIn,
  requesting,
  failed,
  live,
  onRetry,
}: {
  checkIn: CheckIn
  /** A reply request is in flight for THIS check-in. */
  requesting: boolean
  /** The last request for THIS check-in failed. */
  failed: boolean
  /** True on the Today screen, false on History. Drives whether a screen reader is
   *  interrupted — 30 history cards must not fire 30 announcements (#41). */
  live: boolean
  onRetry: () => void
}) {
  const view = replyView({ reply: checkIn.reply, requesting, failed, live })

  if (view.kind === 'none') return null

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
        <button
          type="button"
          onClick={onRetry}
          className="rounded-control font-mono text-xs text-fg underline underline-offset-4 transition-colors duration-150 hover:text-accent focus:text-accent focus:outline-none"
        >
          Try again
        </button>
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
    </div>
  )
}
