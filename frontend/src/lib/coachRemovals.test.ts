/**
 * Oracle suite for issue #48 — the frontend rows (27 and 28).
 *
 * Part of the oracle commit on `fix/48-gate-asks-one-question`, written BEFORE any
 * implementation change exists, from the v2 acceptance table Toby approved on 2026-08-12:
 *   table:    https://github.com/tobyliu2004/coach-bill/issues/48#issuecomment-5268060575
 *   approval: https://github.com/tobyliu2004/coach-bill/issues/48#issuecomment-5268138951
 *
 * #48 removes the `off_topic` label, and with it everything the retract-and-ask-again path
 * was built out of: the mirrored `OFF_TOPIC_REPLY` constant, `isRetractable`, the
 * `retractReply` call, and the "Ask again" affordance. These are REMOVAL rows, so every
 * assertion here is an absence — which makes the controls in each test load-bearing: an
 * absence assertion passes just as happily against a module that failed to load or a file
 * that was read as an empty string. Each test proves it was looking at something first.
 *
 * ⚠️ WHAT THIS TIER CAN AND CANNOT PROVE.
 * vitest here is `src/**\/*.test.ts` in a NODE environment with no DOM emulation
 * (vite.config.ts), unchanged since #18. So:
 *   - Row 27 and the `api.ts` half of row 28 are REAL RUNTIME ASSERTIONS: the module is
 *     imported and its exports are inspected, which survives a rename of the file's
 *     internals and cannot be fooled by a comment.
 *   - The `CoachReply.tsx` half of row 28 is a SOURCE-TEXT SCAN, the same shape as the
 *     backend's rows 22 and 25. A React prop is not introspectable at runtime and the
 *     component cannot be mounted here, so this is a TRIPWIRE ON THE FILE, not an assertion
 *     about the rendered screen. It proves "Ask again" is not in the source; it CANNOT
 *     prove the affordance is gone from the UI — a differently-worded button, or one that
 *     moved to another component, would pass. That half stays a reviewer's eye on the
 *     component, as it has since #18.
 *
 * Every test names the AC row it covers.
 */
import { describe, expect, it } from 'vitest'
import coachReplySource from '../components/CoachReply.tsx?raw'
import { createApi } from './api'
import * as coachView from './coachView'

describe('#48 row 27 — coachView.ts drops the retraction machinery', () => {
  // AC row 27: `frontend/src/lib/coachView.ts` exports no `OFF_TOPIC_REPLY`.
  //
  // A runtime check on the module namespace rather than a text scan: the constant only
  // existed to mirror the backend's, and the backend's is deleted by #48 AC row 22. A
  // mirror whose original is gone is not a mirror, it is a second source of truth.
  it('exports no OFF_TOPIC_REPLY', () => {
    expect(Object.keys(coachView)).not.toContain('OFF_TOPIC_REPLY')
  })

  // AC row 27: ...and no `isRetractable`.
  //
  // This one carried a safety rule ("a crisis reply is never retractable"). It is not being
  // dropped, it is being made unnecessary: after #48 there is no retract endpoint, no
  // DELETE statement in backend/app/ (#48 AC row 25) and no `delete` grant (#48 AC row 24),
  // so nothing can re-roll past the hotlines. A client-side predicate is the weakest of
  // those three guarantees and the only one that was ever a suggestion.
  it('exports no isRetractable', () => {
    expect(Object.keys(coachView)).not.toContain('isRetractable')
  })

  // AC row 27 (the control): `replyView` is untouched and still exported.
  //
  // Without this, both assertions above would pass against a module that exported NOTHING —
  // including a `coachView.ts` someone emptied, or an import that silently resolved to a
  // stub. The seven `replyView` behaviour tests in coachView.test.ts stay exactly as #21
  // wrote them; #48 changes nothing about what that function decides.
  it('still exports replyView, unchanged', () => {
    expect(typeof coachView.replyView).toBe('function')
    expect(coachView.replyView({ reply: null, requesting: true, failed: false, live: false })).toEqual(
      { kind: 'thinking' },
    )
  })
})

describe('#48 row 28 — the retract call and the affordance are gone', () => {
  // AC row 28: `frontend/src/lib/api.ts` has no `retractReply`.
  //
  // Asserted against the object `createApi` actually returns, with injected fakes (the same
  // construction api.test.ts uses), so this is the real wrapper contract and not a search
  // for a word. The endpoint answers 405 after #48 (#48 AC row 23); a client method that
  // can only ever produce a 405 is a trap for the next screen that finds it in the type.
  it('createApi returns no retractReply method', () => {
    const api = createApi({
      getToken: async () => null,
      fetchFn: (async () => new Response(null, { status: 204 })) as unknown as typeof fetch,
      baseUrl: 'http://api.test',
    })

    // The control: the wrapper really was built and really does have its other methods.
    expect(typeof api.deleteCheckIn).toBe('function')
    expect(Object.keys(api)).not.toContain('retractReply')
  })

  // AC row 28: `CoachReply.tsx` has no `onRetract` prop and no "Ask again" affordance.
  //
  // ⚠️ A SOURCE-TEXT SCAN, AND IT IS NOT A RENDERING ASSERTION — see the file docstring.
  // This project's vitest is node-env with no jsdom, so the component cannot be mounted and
  // its props cannot be reflected on. What this proves: those two strings are not in that
  // file. What it does not prove: that no equivalent affordance exists on the screen. The
  // rendered result is reviewed by eye, the same split #18's retro settled.
  //
  // The prop's CALL SITES (History.tsx, AppHome.tsx) are deliberately not scanned here:
  // once the prop is gone, passing it is a type error, so `tsc -b` in `npm run build` is
  // already a stronger check on those than a substring search would be.
  //
  // The source arrives through Vite's `?raw` import rather than `node:fs`, because this
  // project's tsconfig scopes `types` to `vite/client` — no node typings — and widening
  // that for a test would be changing config to suit a test.
  it('CoachReply.tsx mentions neither onRetract nor "Ask again"', () => {
    const component: string = coachReplySource

    // The controls: the file really was read, and it really is the component. An absence
    // assertion against an empty string passes while proving nothing.
    expect(component.length).toBeGreaterThan(200)
    expect(component).toContain('export function CoachReply')

    expect(component).not.toContain('onRetract')
    expect(component).not.toContain('Ask again')
    // `isRetractable` is imported by this component today; row 27 deletes the export, so a
    // leftover import here would be a build error — assert it directly anyway, because a
    // dead import is how a deleted feature keeps a foothold.
    expect(component).not.toContain('isRetractable')
  })
})
