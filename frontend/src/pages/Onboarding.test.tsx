/**
 * Oracle suite for issue #46 — the APPROVED AMENDMENT, row 15.
 *
 * DIRECTION: NARROWING (approved on issue #46, 2026-09-03). Row 15 adds a case that can only
 * newly FAIL. Written before the fix exists.
 *
 * THE ROW: the post-onboarding profile refresh fails after `updateMe` succeeded → the
 * onboarding form is NOT left disabled; a visible error and a working way to retry. Negative
 * half: it must not render a permanently disabled "Saving…".
 *
 * WHERE THE FIX LIVES, as ruled: `Onboarding.tsx` — the screen that owns the submit owns its
 * own error state. `ProtectedRoute`'s rule ("blank the app only when the alternative is a
 * blank app") stands, so this suite asserts on the FORM and never on the guard.
 *
 * TWO ARRANGEMENTS, because "the refresh fails" has two shapes and the row names neither:
 *
 *   A. `refreshProfile()` REJECTS.
 *   B. `refreshProfile()` RESOLVES and reports the failure by flipping `profileError`.
 *
 * B is what the real provider does — verified against the real `AuthProvider`, whose
 * `refreshProfile` catches the failure and resolves — so B is the arrangement the user
 * actually meets and A is the defensive one. Both are the same row: the row is written as
 * observable behaviour of the form, and the form cannot be allowed to strand the user in
 * either. Arrangement B needs a context whose `profileError` can change after the render, so
 * it uses a small local provider instead of `renderWithAuth` (which builds its value once, on
 * purpose); function identities are still stable across renders, for the same reason.
 *
 * ENCODING, category not copy (CLAUDE.md: describe the category, do not enumerate):
 *   "a visible error"        → `errorAffordances`: an assertive live region, or a state
 *                              element naming a failure, carrying an actual message. Never a
 *                              phrase — a rewording must not turn this red.
 *   "not left disabled"      → `busyControls` must be EMPTY once the attempt has finished:
 *                              nothing may still claim to be working. This is the direct
 *                              encoding of "not a permanently disabled Saving…", and unlike
 *                              "some control somewhere is enabled" it cannot be satisfied by
 *                              the unrelated chips further up the form.
 *   "a working way to retry" → activating the control ISSUES ANOTHER ATTEMPT. Asserted as a
 *                              call, because an enabled control that does nothing is the same
 *                              dead end with better manners.
 */
import type { ReactElement } from 'react'
import { useMemo, useRef, useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { AuthContext, type AuthContextValue } from '../auth/context'
import { ApiError, type Profile } from '../lib/api'
import {
  aProfile,
  busyControls,
  errorAffordances,
  flush,
  renderWithAuth,
  wayOutControls,
} from '../test/harness'

vi.mock('../lib/client', () => ({
  api: {
    getMe: vi.fn(),
    updateMe: vi.fn(),
    createCheckIn: vi.fn(),
    listCheckIns: vi.fn(),
    getTrends: vi.fn(),
    requestReply: vi.fn(),
    deleteCheckIn: vi.fn(),
  },
}))

import { api } from '../lib/client'
import Onboarding from './Onboarding'

const updateMe = vi.mocked(api.updateMe)

/** A brand-new account: nothing filled in yet, so the onboarding form is the screen's job. */
function aFreshProfile(): Profile {
  return aProfile({ display_name: null, goal: null, consented_at: null })
}

/** What `updateMe` answers with — in this row the SAVE SUCCEEDS. */
function aSavedProfile(): Profile {
  return aProfile({ display_name: 'oracle-onboarded', goal: 'oracle-goal' })
}

/** The form's own scope, or the whole screen if it does not use a `<form>` element. */
function formScope(container: HTMLElement): HTMLElement {
  return container.querySelector('form') ?? container
}

/** Fill the form the way a user would, structurally — never by label copy. */
function fillForm(container: HTMLElement): void {
  const scope = formScope(container)
  for (const field of Array.from(
    scope.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('input, textarea'),
  )) {
    if (field instanceof HTMLInputElement && (field.type === 'checkbox' || field.type === 'radio')) {
      if (!field.checked) fireEvent.click(field)
      continue
    }
    fireEvent.change(field, { target: { value: 'oracle-goal' } })
  }
}

/** The control that submits the form. */
function submitControl(container: HTMLElement): HTMLButtonElement {
  const submit = formScope(container).querySelector<HTMLButtonElement>('button[type="submit"]')
  if (submit === null) throw new Error('the onboarding form has no submit control')
  return submit
}

async function submit(container: HTMLElement): Promise<void> {
  await act(async () => {
    fireEvent.click(submitControl(container))
    await Promise.resolve()
  })
  await flush()
}

/**
 * Arrangement B: the provider's real failure channel. `refreshProfile` resolves; the failure
 * shows up as `profileError` flipping true on the next render, with `profile` unchanged.
 */
function FailingRefreshProvider({ children }: { children: ReactElement }): ReactElement {
  const [profileError, setProfileError] = useState(false)
  const profile = useRef(aFreshProfile()).current
  const fns = useRef({
    refreshProfile: (): Promise<void> => {
      setProfileError(true)
      return Promise.resolve()
    },
    signOut: (): Promise<void> => Promise.resolve(),
  }).current
  const value: AuthContextValue = useMemo(
    () => ({
      status: 'signedIn',
      session: null,
      profile,
      profileError,
      profileLoading: false,
      refreshProfile: fns.refreshProfile,
      signOut: fns.signOut,
    }),
    [profile, profileError, fns],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

interface Arrangement {
  readonly name: string
  /** Mount the screen with the refresh failing in this arrangement's way. */
  readonly mount: () => HTMLElement
  /** How many attempts have been made — used only to prove the retry actually retries. */
  readonly attempts: () => number
}

const REJECTS: Arrangement = (() => {
  const refreshProfile = vi.fn(() => Promise.reject(new ApiError(500, '/me failed')))
  return {
    name: 'the refresh rejects',
    mount: () =>
      renderWithAuth(
        <Onboarding />,
        {
          status: 'signedIn',
          profile: aFreshProfile(),
          profileError: false,
          profileLoading: false,
          refreshProfile,
        },
        ['/onboarding'],
      ).container,
    attempts: () => updateMe.mock.calls.length + refreshProfile.mock.calls.length,
  }
})()

const REPORTS_PROFILE_ERROR: Arrangement = {
  name: 'the refresh resolves and reports profileError (what the real provider does)',
  mount: () =>
    render(
      <MemoryRouter initialEntries={['/onboarding']}>
        <FailingRefreshProvider>
          <Onboarding />
        </FailingRefreshProvider>
      </MemoryRouter>,
    ).container,
  attempts: () => updateMe.mock.calls.length,
}

const ARRANGEMENTS: readonly Arrangement[] = [REJECTS, REPORTS_PROFILE_ERROR]

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('#46 row 15 — the profile refresh fails after updateMe succeeded', () => {
  for (const arrangement of ARRANGEMENTS) {
    it(`row 15 (${arrangement.name}): a visible error, and nothing left stuck in flight`, async () => {
      updateMe.mockResolvedValue(aSavedProfile())
      const container = arrangement.mount()

      fillForm(container)
      await submit(container)

      // Precondition, not the row: this row is about a save that SUCCEEDED.
      expect(updateMe).toHaveBeenCalledTimes(1)

      // The negative half: no permanently disabled "Saving…" — the attempt is over, so no
      // control may still be claiming to be working.
      expect(
        busyControls(formScope(container)).map((el) => el.outerHTML),
        'the attempt finished — the form must not be left switched off mid-submit',
      ).toEqual([])
      // ...and the user is told, rather than left to guess why nothing happened.
      expect(
        errorAffordances(container).length,
        'a failed refresh must surface in the form, not silently',
      ).toBeGreaterThan(0)
      expect(wayOutControls(container).length).toBeGreaterThan(0)
    })

    it(`row 15 (${arrangement.name}): the way out actually retries`, async () => {
      updateMe.mockResolvedValue(aSavedProfile())
      const container = arrangement.mount()

      fillForm(container)
      await submit(container)

      const before = arrangement.attempts()
      await submit(container)

      expect(
        arrangement.attempts() - before,
        'an enabled control that does nothing is the same dead end with better manners',
      ).toBeGreaterThan(0)
    })
  }
})

/**
 * The rules row 15 leans on, proved to reject what they exist to reject. Without this,
 * `errorAffordances` could be a rule that matches anything and row 15 could not fail.
 */
describe('#46 row 15: the form-state rules themselves', () => {
  it('errorAffordances rejects a silent failure, a polite status, and an empty marker', () => {
    // A form that failed and said nothing — exactly what row 15 exists to catch.
    const silent = document.createElement('div')
    silent.innerHTML = '<form><input type="text"><button type="submit">Save</button></form>'
    expect(errorAffordances(silent)).toHaveLength(0)

    const notAnError = document.createElement('div')
    notAnError.innerHTML = '<div role="status">Saving…</div><div role="alert"></div>'
    expect(errorAffordances(notAnError)).toHaveLength(0)

    const nonFailureState = document.createElement('div')
    nonFailureState.innerHTML = '<div data-state="empty">Nothing here yet.</div>'
    expect(errorAffordances(nonFailureState)).toHaveLength(0)

    const real = document.createElement('div')
    real.innerHTML = '<div role="alert">We could not refresh your profile.</div>'
    expect(errorAffordances(real)).toHaveLength(1)

    const stateMarked = document.createElement('div')
    stateMarked.innerHTML = '<div data-state="load-failed">Something went wrong.</div>'
    expect(errorAffordances(stateMarked)).toHaveLength(1)
  })

  it('busyControls catches a stuck submit and passes a finished form', () => {
    // The exact negative half of row 15.
    const stuck = document.createElement('div')
    stuck.innerHTML = '<form><button type="submit" disabled>Saving…</button></form>'
    expect(busyControls(stuck)).toHaveLength(1)

    const alsoStuck = document.createElement('div')
    alsoStuck.innerHTML = '<form><button type="submit" aria-busy="true">Saving…</button></form>'
    expect(busyControls(alsoStuck)).toHaveLength(1)

    const finished = document.createElement('div')
    finished.innerHTML = '<form><button type="submit">Meet your coach</button></form>'
    expect(busyControls(finished)).toHaveLength(0)
  })
})
