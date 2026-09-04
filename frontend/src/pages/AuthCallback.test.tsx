/**
 * Oracle suite for issue #46 — the /auth/callback screen (rows 1, 2, 4, 5, 6, and its half
 * of row 10). Rows 3 and 8 live in `auth/AuthProvider.test.tsx`; row 7 and row 9 live in
 * `auth/ProtectedRoute.test.tsx`.
 *
 * Written before the implementation exists.
 *
 * HOW THE STATES ARE IDENTIFIED, and why it is not by copy. The four branches of this screen
 * differ in what the user can DO, which is the thing the bug is about — the reported defect
 * is not "the wrong sentence", it is "a dead end with no way out". So:
 *   - the profile-error state  = an error message + a retry control + a way back to sign-in
 *   - the quiet waiting state  = something visible, no retry control, no error
 *   - the dead-link state      = the link error's own text + a way back to sign-in
 * A wording change must not turn any of these tests red, and a different screen saying the
 * same words must not turn them green.
 *
 * ENCODING NOTE for the reviewer: "an error message" is asserted as `role="alert"`, the same
 * encoding the #43 contract uses for the list screens' failure branch. That is the only
 * non-copy way to check it, but it IS an encoding choice on top of the approved row — see
 * the report.
 *
 * AMENDMENT DIRECTION — READ THIS BEFORE TRUSTING A GREEN RUN. Rows 5 and 6 are WIDENING
 * amendments, the direction `CLAUDE.md` calls the dangerous one: they can newly PASS. They are
 * green the moment they are written, because they pin behaviour that ALREADY EXISTS on `main`
 * — the 6s dead-link escape, and link-error precedence — rather than behaviour this branch
 * adds. They exist so the fix cannot silently break them, and for no other reason. Do not read
 * six green auth tests as six things this branch fixed.
 *
 * The rows here that must be RED before the fix and green after are 1, 2, and row 10's
 * profile-error case.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, within } from '@testing-library/react'
import { hasVisibleTreatment, renderWithAuth, wayOutControls } from '../test/harness'
import AuthCallback from './AuthCallback'

const CALLBACK = '/auth/callback'
/** A unique string only this test could have put in the URL — data, not copy. */
const LINK_ERROR = 'oracle-sentinel-link-expired'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  window.location.hash = ''
})

/** Links that actually go back to sign-in, found structurally rather than by their label. */
function loginLinks(container: HTMLElement): HTMLAnchorElement[] {
  return Array.from(container.querySelectorAll<HTMLAnchorElement>('a[href]')).filter(
    (a) => a.getAttribute('href') === '/login',
  )
}

describe('#46 /auth/callback', () => {
  it('row 1: signed in, profile fetch failed → an error message AND a retry control', () => {
    const { container } = renderWithAuth(
      <AuthCallback />,
      { status: 'signedIn', profile: null, profileError: true, profileLoading: false },
      [CALLBACK],
    )

    expect(within(container).getAllByRole('alert').length).toBeGreaterThan(0)
    const retry = within(container).getByRole('button', { name: /try again|retry/i })
    expect(retry.hasAttribute('disabled')).toBe(false)
    expect(loginLinks(container).length).toBeGreaterThan(0)
  })

  it('row 2: that error state, retry activated → refreshProfile runs again', () => {
    const refreshProfile = vi.fn(() => Promise.resolve())

    const { container } = renderWithAuth(
      <AuthCallback />,
      { status: 'signedIn', profile: null, profileError: true, profileLoading: false, refreshProfile },
      [CALLBACK],
    )

    fireEvent.click(within(container).getByRole('button', { name: /try again|retry/i }))

    expect(refreshProfile).toHaveBeenCalledTimes(1)
  })

  it('row 4: signed in, profile still in flight, before the timeout → the quiet treatment', () => {
    const { container } = renderWithAuth(
      <AuthCallback />,
      { status: 'signedIn', profile: null, profileError: false, profileLoading: true },
      [CALLBACK],
    )

    // Something is on screen — a slow network must not render as a blank page either.
    expect(hasVisibleTreatment(container)).toBe(true)
    // ...but a slow network is not an error, and offers nothing to retry yet.
    expect(within(container).queryByRole('alert')).toBeNull()
    expect(within(container).queryByRole('button', { name: /try again|retry/i })).toBeNull()
  })

  it('row 5 (regression guard): NOT signed in, past 6s → the dead-link message + a way back', () => {
    vi.useFakeTimers()

    const { container } = renderWithAuth(
      <AuthCallback />,
      { status: 'signedOut', profile: null, profileError: false, profileLoading: false },
      [CALLBACK],
    )

    act(() => {
      vi.advanceTimersByTime(6000)
    })

    expect(hasVisibleTreatment(container)).toBe(true)
    expect(loginLinks(container).length).toBeGreaterThan(0)
  })

  it('row 6 (regression guard): a link error in the QUERY STRING wins over the profile error', () => {
    const { container } = renderWithAuth(
      <AuthCallback />,
      { status: 'signedIn', profile: null, profileError: true, profileLoading: false },
      [`${CALLBACK}?error_description=${LINK_ERROR}`],
    )

    // The link error's own description is on screen — that is DATA we put in the URL, so it
    // identifies the branch without pinning any wording around it.
    expect(container.textContent ?? '').toContain(LINK_ERROR)
    // ...and it is the link-error branch, not the profile-error one: nothing to retry, since
    // retrying the profile fetch cannot fix a dead link.
    expect(within(container).queryByRole('button', { name: /try again|retry/i })).toBeNull()
    expect(loginLinks(container).length).toBeGreaterThan(0)
  })

  it('row 6 (regression guard): a link error in the URL FRAGMENT wins over the profile error', () => {
    window.location.hash = `#error_description=${LINK_ERROR}`

    const { container } = renderWithAuth(
      <AuthCallback />,
      { status: 'signedIn', profile: null, profileError: true, profileLoading: false },
      [CALLBACK],
    )

    expect(container.textContent ?? '').toContain(LINK_ERROR)
    expect(within(container).queryByRole('button', { name: /try again|retry/i })).toBeNull()
    expect(loginLinks(container).length).toBeGreaterThan(0)
  })

  it('row 10: the profile-error state on /auth/callback is not a dead end', () => {
    const { container } = renderWithAuth(
      <AuthCallback />,
      { status: 'signedIn', profile: null, profileError: true, profileLoading: false },
      [CALLBACK],
    )

    // The general form: at least one enabled control or real link leads out. This is what
    // "terminal" means operationally — not which sentence is printed.
    expect(wayOutControls(container).length).toBeGreaterThan(0)
  })

  it('row 10: the timed-out dead-link state on /auth/callback is not a dead end', () => {
    vi.useFakeTimers()

    const { container } = renderWithAuth(
      <AuthCallback />,
      { status: 'signedOut', profile: null, profileError: false, profileLoading: false },
      [CALLBACK],
    )

    act(() => {
      vi.advanceTimersByTime(6000)
    })

    expect(wayOutControls(container).length).toBeGreaterThan(0)
  })
})
