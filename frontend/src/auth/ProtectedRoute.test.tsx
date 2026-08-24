/**
 * Oracle suite for issue #46 — the route guard (rows 7, 9, and its half of row 10).
 *
 * Written before the implementation exists.
 *
 * Row 7 is a pure regression guard: the existing profile-error screen must survive the fix.
 * Row 9 is the new behaviour: while the profile is in flight the guard currently returns
 * `null`, which is the #43/#46 bug in its purest form — a state that renders as a blank page.
 *
 * AMENDMENT DIRECTION — ROW 7 IS A WIDENING AMENDMENT, the direction `CLAUDE.md` calls the
 * dangerous one: it can newly PASS. It is green on arrival. The issue's draft table said
 * "`profileError` exists in the context and no screen reads it", which implied work to do;
 * that turned out to be FALSE — `ProtectedRoute` has rendered the error with a "Try again"
 * button since `f2abdc0`, and all three app screens sit inside it. The real dead end is
 * `/auth/callback`, the one authed-ish route OUTSIDE this guard. So the row was rewritten
 * from "build this" into "prove this still works", and a green row 7 is evidence of nothing
 * this branch did. Recorded on issue #46 with the same direction.
 *
 * Row 9 and row 10's in-flight case are the ones that must be RED before the fix.
 */
import type { ReactElement } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, within } from '@testing-library/react'
import { Route, Routes } from 'react-router'
import {
  type FakeAuth,
  hasVisibleTreatment,
  renderWithAuth,
  wayOutControls,
} from '../test/harness'
import { ProtectedRoute } from './ProtectedRoute'

/** Marker for "the guarded screen itself rendered" — never a UI string a user would read. */
const GUARDED = 'oracle-guarded-screen'
const APP_PATHS = ['/app', '/history', '/trends'] as const

function guardTree(): ReactElement {
  return (
    <Routes>
      <Route element={<ProtectedRoute />}>
        {APP_PATHS.map((path) => (
          <Route key={path} path={path} element={<div>{GUARDED}</div>} />
        ))}
      </Route>
      <Route path="/login" element={<div>oracle-login-screen</div>} />
      <Route path="/onboarding" element={<div>oracle-onboarding-screen</div>} />
    </Routes>
  )
}

function renderGuard(auth: Partial<FakeAuth>, path: string) {
  return renderWithAuth(guardTree(), auth, [path])
}

afterEach(cleanup)

describe('#46 ProtectedRoute', () => {
  for (const path of APP_PATHS) {
    it(`row 7 (regression guard): profile fetch failed on ${path} → the error + a retry still render`, () => {
      const { container } = renderGuard(
        { status: 'signedIn', profile: null, profileError: true, profileLoading: false },
        path,
      )

      expect(hasVisibleTreatment(container)).toBe(true)
      const retry = within(container).getByRole('button', { name: /try again|retry/i })
      expect(retry.hasAttribute('disabled')).toBe(false)
      // The guarded screen itself must NOT be behind the error.
      expect(within(container).queryByText(GUARDED)).toBeNull()
    })
  }

  it('row 9: signed in, profile in flight on a protected path → a loading treatment, not a blank page', () => {
    const { container } = renderGuard(
      { status: 'signedIn', profile: null, profileError: false, profileLoading: true },
      '/app',
    )

    expect(hasVisibleTreatment(container)).toBe(true)
    // ...and it is a WAITING treatment, not the real screen leaking through early.
    expect(within(container).queryByText(GUARDED)).toBeNull()
  })

  it('row 10: the profile-error state behind the guard is not a dead end', () => {
    const { container } = renderGuard(
      { status: 'signedIn', profile: null, profileError: true, profileLoading: false },
      '/app',
    )

    expect(wayOutControls(container).length).toBeGreaterThan(0)
  })
})

// A guard against the assertion above being vacuous: `hasVisibleTreatment` must reject a
// blank render, which is exactly what row 9 is written to catch. Proved here rather than
// assumed, because "a test that cannot fail is not a test".
describe('#46 row 9: the blank-page rule itself', () => {
  it('hasVisibleTreatment rejects an empty container', () => {
    const empty = document.createElement('div')
    expect(hasVisibleTreatment(empty)).toBe(false)

    const withText = document.createElement('div')
    withText.textContent = 'anything at all'
    expect(hasVisibleTreatment(withText)).toBe(true)

    const withSkeleton = document.createElement('div')
    withSkeleton.innerHTML = '<div data-component="skeleton" role="status" aria-label="Loading"></div>'
    expect(hasVisibleTreatment(withSkeleton)).toBe(true)
  })
})

// Same for the way-out rule (#46 row 10): it must reject a state with nothing to click.
describe('#46 row 10: the way-out rule itself', () => {
  it('wayOutControls rejects a dead end and accepts a real control or link', () => {
    const deadEnd = document.createElement('div')
    deadEnd.innerHTML = '<p>Signing you in…</p><button type="button" disabled>Try again</button><a>no href</a>'
    expect(wayOutControls(deadEnd)).toHaveLength(0)

    const wayOut = document.createElement('div')
    wayOut.innerHTML = '<a href="/login">Back</a>'
    expect(wayOutControls(wayOut)).toHaveLength(1)
  })
})
