/**
 * Oracle suite for issue #21, section G — the frontend rows (38-41).
 *
 * Part of commit #1 on `feat/21-coach-replies`, written BEFORE any implementation exists.
 * `./coachView` does not exist yet: this file failing to resolve it is the CORRECT initial
 * failure.
 *
 * Why a pure module rather than a component test: vitest here is `src/**` + node env with
 * no DOM emulation (vite.config.ts), so a `.tsx` component cannot be mounted. Rows 38-41
 * are DECISIONS — which state is this, and should it be announced — so they live in
 * `replyView` and are tested; the markup that renders each decision is reviewed by eye.
 * That is the same split `lib/checkInView.ts::listView` already makes, and the #18 retro's
 * finding is why: every frontend bug that ticket shipped was an error state that no test
 * could reach.
 *
 * ROWS THIS FILE CANNOT HONESTLY COVER, stated rather than faked:
 *   - Row 41's RENDERING half ("announced to screen readers") — whether the announcement
 *     actually reaches an `aria-live` region is a DOM assertion, and this project has no
 *     jsdom. What IS covered here is the decision underneath it: WHETHER to announce, which
 *     is the half that goes wrong (`/app` announces, a 30-card `/history` mount must not
 *     fire 30 announcements — the trap open as #41).
 *   - Row 42 entirely (reply text renders as plain text, wrapped, no HTML interpretation).
 *     That is a property of the JSX — React escapes interpolated text unless someone reaches
 *     for `dangerouslySetInnerHTML` — and there is no node-env assertion that can tell the
 *     two apart. It needs either jsdom or a reviewer's eye on the component; flagged for
 *     Toby rather than covered by something weaker that would look like coverage.
 *
 * Every test names the AC row it covers.
 */
import { describe, expect, it } from 'vitest'
import type { CheckIn } from './api'
import { replyView } from './coachView'

const REPLY: NonNullable<CheckIn['reply']> = {
  id: '4f0f9a10-0000-4000-8000-000000000000',
  content: 'Solid session. Keep the bar path over midfoot next time.',
  created_at: '2026-08-02T12:00:00Z',
}

/** The idle state: nothing in flight, nothing failed, no reply, not a live screen. */
function state(overrides: Partial<Parameters<typeof replyView>[0]> = {}) {
  return { reply: null, requesting: false, failed: false, live: false, ...overrides }
}

describe('replyView', () => {
  // AC row 38: a request is in flight -> 'thinking', which renders "Bill is reading your
  // check-in…". A blank space during a 3-6s wait reads as "it's broken" — #43's bug class.
  it("reports 'thinking' while the request is in flight", () => {
    expect(replyView(state({ requesting: true }))).toEqual({ kind: 'thinking' })
    expect(replyView(state({ requesting: true, live: true }))).toEqual({ kind: 'thinking' })
  })

  // AC row 39: after a 503 -> 'failed', which renders a muted message PLUS a retry
  // affordance. Never silence, never the empty state. The #18 retro's headline finding.
  it("reports 'failed' after a failed request", () => {
    expect(replyView(state({ failed: true }))).toEqual({ kind: 'failed', announce: false })
  })

  // AC row 40: a history item with no reply -> 'none', which renders nothing.
  it("reports 'none' for a check-in that has no reply", () => {
    expect(replyView(state())).toEqual({ kind: 'none' })
  })

  // AC rows 39 + 40 together: "no reply" and "reply failed" must not collapse into the same
  // pixels. Asserting each shape separately would still pass an implementation that mapped
  // both onto one value — which is the bug — so assert the DISTINCTION itself.
  it('never collapses a failed reply into the empty state', () => {
    const failed = replyView(state({ failed: true }))
    const none = replyView(state())
    expect(failed.kind).not.toBe(none.kind)
  })

  // AC row 41: a reply present -> 'reply' carrying the reply's own text (the content the
  // component renders, and nothing else).
  it("reports 'reply' with the stored content", () => {
    expect(replyView(state({ reply: REPLY, live: true }))).toEqual({
      kind: 'reply',
      content: REPLY.content,
      announce: true,
    })
  })

  // AC row 41: a reply arriving on `/app` (live) is announced to screen readers...
  it('announces a reply on the live screen', () => {
    const view = replyView(state({ reply: REPLY, live: true }))
    expect(view).toEqual({ kind: 'reply', content: REPLY.content, announce: true })
  })

  // ...and a 30-day `/history` mount does NOT fire 30 announcements. Same component, two
  // contexts — the exact trap open as issue #41. Asserted as a COUNT over 30 mounted cards:
  // a single-card assertion would pass an implementation that announces every card.
  it('does not announce any of 30 history cards', () => {
    const views = Array.from({ length: 30 }, (_, index) =>
      replyView(state({ reply: { ...REPLY, id: `id-${index}` }, live: false })),
    )

    expect(views).toHaveLength(30)
    expect(views.every((view) => view.kind === 'reply')).toBe(true)
    const announcing = views.filter((view) => 'announce' in view && view.announce)
    expect(announcing).toHaveLength(0)
  })

  // AC row 41 applied to the failure state: `announce` follows `live` there too. The table
  // states the announce rule once (row 41), and the approved `ReplyView` type carries an
  // `announce` flag on `failed` — so 30 history cards that all failed must not fire 30
  // announcements either, for exactly the reason row 41 gives.
  it('announces a failure on the live screen but not on history', () => {
    expect(replyView(state({ failed: true, live: true }))).toEqual({
      kind: 'failed',
      announce: true,
    })
    expect(replyView(state({ failed: true, live: false }))).toEqual({
      kind: 'failed',
      announce: false,
    })
  })
})
