/**
 * Oracle suite for issue #46 — the APPROVED AMENDMENT, row 14.
 *
 * DIRECTION: NARROWING (approved on issue #46, 2026-09-03). Row 14 adds a case that can only
 * newly FAIL. Written before the fix exists.
 *
 * THE ROW: `profileError` is true AND a retry is in flight → the error state stays on screen
 * with at least one ENABLED way out, and the retry shows its in-flight state on its OWN
 * control. Negative half: it must not render a screen whose every control is disabled.
 *
 * WHY IT EXISTS: `d69c46f` deliberately removed a `!auth.profileLoading` gate from both error
 * branches, and re-adding it leaves the suite green — a decision nothing defends is a decision
 * that gets silently undone. The gate's failure mode is this ticket's own defect one level
 * down: an in-flight state rendered as some other state, here by swapping the error screen for
 * the waiting screen the moment the user presses retry, on precisely the slow backend that
 * produced the error.
 *
 * ENCODING, and why none of it is copy (CLAUDE.md: describe the category, do not enumerate):
 *
 *   "at least one enabled way out"  → `wayOutControls`, the helper Toby's ruling names, already
 *                                     self-tested in both directions at
 *                                     `ProtectedRoute.test.tsx:97-123`. Not "a button labelled
 *                                     Try again": a rewording must not turn this red and a
 *                                     disabled lookalike must not turn it green.
 *
 *   "the error state stays"         → a DIFFERENTIAL against the same screen's own waiting
 *                                     render, not a phrase. If the error branch gets gated on
 *                                     `!profileLoading` again, the (error + loading) render
 *                                     collapses into exactly the (loading, no error) render,
 *                                     and this comparison is what notices. Taking the baseline
 *                                     from the implementation's own output means no hardcoded
 *                                     sentence can go stale, and it cannot pass just because
 *                                     the screen happens to be non-blank.
 *
 *   "on its own control"            → `busyControls`: a control marked busy or out of service
 *                                     for the duration. Deliberately NOT satisfied by a
 *                                     `role="status"` spinner elsewhere in the container —
 *                                     "the whole screen went to loading" is the thing the row
 *                                     rejects, so it must not be able to satisfy it.
 */
import type { ReactElement } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup } from '@testing-library/react'
import { Route, Routes } from 'react-router'
import {
  busyControls,
  type FakeAuth,
  hasVisibleTreatment,
  normalizedText,
  renderWithAuth,
  wayOutControls,
} from '../test/harness'
import { ProtectedRoute } from './ProtectedRoute'
import AuthCallback from '../pages/AuthCallback'

/** Marker for "the guarded screen itself rendered" — never a UI string a user would read. */
const GUARDED = 'oracle-guarded-screen'

function guardTree(): ReactElement {
  return (
    <Routes>
      <Route element={<ProtectedRoute />}>
        <Route path="/app" element={<div>{GUARDED}</div>} />
      </Route>
      <Route path="/login" element={<div>oracle-login-screen</div>} />
      <Route path="/onboarding" element={<div>oracle-onboarding-screen</div>} />
    </Routes>
  )
}

interface ScreenSpec {
  readonly name: string
  readonly path: string
  readonly tree: () => ReactElement
}

/** Both screens that render `profileError`. The closed set is enumerated here and nowhere else. */
const SCREENS: readonly ScreenSpec[] = [
  { name: 'ProtectedRoute', path: '/app', tree: guardTree },
  { name: '/auth/callback', path: '/auth/callback', tree: () => <AuthCallback /> },
]

const SIGNED_IN: Partial<FakeAuth> = { status: 'signedIn', profile: null }

afterEach(cleanup)

describe('#46 row 14 — profileError true AND a retry in flight', () => {
  for (const screen of SCREENS) {
    it(`row 14: ${screen.name} keeps the error state, with an enabled way out`, () => {
      // The same screen's pure waiting render, used only as the baseline to differ from.
      const waiting = renderWithAuth(
        screen.tree(),
        { ...SIGNED_IN, profileError: false, profileLoading: true },
        [screen.path],
      )
      const waitingText = normalizedText(waiting.container)
      cleanup()

      // The row's state: the last completed attempt failed, and a retry is in flight.
      const { container } = renderWithAuth(
        screen.tree(),
        { ...SIGNED_IN, profileError: true, profileLoading: true },
        [screen.path],
      )

      expect(hasVisibleTreatment(container)).toBe(true)
      expect(
        normalizedText(container),
        'the error state must survive the retry — it must not collapse into the waiting state',
      ).not.toBe(waitingText)
      // The negative half of the row: not a screen whose every control is disabled.
      expect(
        wayOutControls(container).length,
        'a retry in flight must still leave at least one enabled way out',
      ).toBeGreaterThan(0)
    })

    it(`row 14: ${screen.name} shows the retry's in-flight state on its own control`, () => {
      const { container } = renderWithAuth(
        screen.tree(),
        { ...SIGNED_IN, profileError: true, profileLoading: true },
        [screen.path],
      )

      expect(
        busyControls(container).length,
        "the retry's in-flight state belongs on the control the user pressed, not on the screen",
      ).toBeGreaterThan(0)
    })
  }
})

/**
 * The two rules row 14 leans on, proved to reject what they exist to reject. Without this,
 * `busyControls` could be a rule that matches anything and row 14 could not fail.
 */
describe('#46 row 14: the in-flight-control rule itself', () => {
  it('busyControls rejects a screen-level spinner and accepts a marked control', () => {
    // The exact behaviour the row forbids: the busy-ness shown by the SCREEN, not the control.
    const screenLevel = document.createElement('div')
    screenLevel.innerHTML =
      '<div role="status" aria-label="Loading"></div><button type="button">Try again</button>'
    expect(busyControls(screenLevel)).toHaveLength(0)

    const ariaBusy = document.createElement('div')
    ariaBusy.innerHTML = '<button type="button" aria-busy="true">Try again</button>'
    expect(busyControls(ariaBusy)).toHaveLength(1)

    const dataBusy = document.createElement('div')
    dataBusy.innerHTML = '<button type="button" data-busy="true">Try again</button>'
    expect(busyControls(dataBusy)).toHaveLength(1)

    // A control taken out of service for the duration also shows it on itself.
    const outOfService = document.createElement('div')
    outOfService.innerHTML = '<button type="button" disabled>Trying…</button>'
    expect(busyControls(outOfService)).toHaveLength(1)
  })

  it('normalizedText distinguishes two different renders and ignores whitespace', () => {
    const a = document.createElement('div')
    a.innerHTML = '<p>Signing  you\n in…</p>'
    const b = document.createElement('div')
    b.innerHTML = '<p>Signing you in…</p>'
    const c = document.createElement('div')
    c.innerHTML = '<p>We could not load your profile.</p>'

    expect(normalizedText(a)).toBe(normalizedText(b))
    expect(normalizedText(a)).not.toBe(normalizedText(c))
  })
})
