/**
 * Oracle suite for issue #46 — the provider (rows 3 and 8, plus row 9's provider half).
 *
 * Written before the implementation exists. This is the only suite that mounts the REAL
 * `AuthProvider`, so `lib/supabase` is mocked (hoisted, so the real module — which throws at
 * import time without env vars — never loads) alongside `lib/client`.
 *
 * Row 3 is a regression guard: a 401 means the token is unusable, so the user is signed out
 * rather than shown an error screen they cannot act on.
 * Row 8 is the double-request bug: `refreshProfile` clears `profileError` before it fetches,
 * and the auto-fetch effect fires on `!profileError`, so one retry currently issues two
 * concurrent `/me` requests.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render } from '@testing-library/react'
import { ApiAuthError, ApiError, type Profile } from '../lib/api'
import { flush, pending } from '../test/harness'

vi.mock('../lib/supabase', () => ({
  supabase: {
    auth: {
      // A live session: the profile is what fails in this bug, never the session.
      getSession: vi.fn(() => Promise.resolve({ data: { session: { user: { id: 'user-1' } } } })),
      onAuthStateChange: vi.fn(() => ({
        data: { subscription: { unsubscribe: vi.fn() } },
      })),
      signOut: vi.fn(() => Promise.resolve({ error: null })),
    },
  },
}))

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
import { supabase } from '../lib/supabase'
import { AuthProvider } from './AuthProvider'
import { useAuth } from './useAuth'
import type { AuthContextValue } from './context'

const getMe = vi.mocked(api.getMe)
const signOut = vi.mocked(supabase.auth.signOut)

/**
 * Read `profileLoading` off the context without an `any` and without depending on the field
 * existing yet: the contract says `AuthContextValue` gains it, and this suite is written
 * before it does. `undefined` here means "the provider never reported it".
 */
function profileLoadingOf(auth: object): boolean | undefined {
  const value = (auth as { profileLoading?: unknown }).profileLoading
  return typeof value === 'boolean' ? value : undefined
}

/** Renders the context out as data so tests assert on values, not on any screen's markup. */
function Probe() {
  const auth: AuthContextValue = useAuth()
  return (
    <div>
      <span data-testid="profile-error">{String(auth.profileError)}</span>
      <span data-testid="profile-loading">{String(profileLoadingOf(auth))}</span>
      <button type="button" onClick={() => void auth.refreshProfile()}>
        retry
      </button>
    </div>
  )
}

function renderProvider() {
  return render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('#46 AuthProvider', () => {
  it('row 3 (regression guard): the profile fetch 401s → signed out, no error state', async () => {
    getMe.mockRejectedValue(new ApiAuthError('session rejected by the API'))

    const { getByTestId } = renderProvider()
    await flush()

    expect(signOut).toHaveBeenCalledTimes(1)
    // A 401 is not an in-app error to retry — it is a sign-out.
    expect(getByTestId('profile-error').textContent).toBe('false')
  })

  it('row 8: retry activated exactly once → exactly ONE /me request is issued', async () => {
    getMe.mockRejectedValue(new ApiError(500, '/me failed'))

    const { getByRole, getByTestId } = renderProvider()
    await flush()

    expect(getByTestId('profile-error').textContent).toBe('true')
    const before = getMe.mock.calls.length

    await act(async () => {
      fireEvent.click(getByRole('button', { name: /retry/i }))
    })
    await flush()

    expect(
      getMe.mock.calls.length - before,
      'one retry must issue one request — clearing profileError re-arms the auto-fetch effect',
    ).toBe(1)
  })

  it("row 9 (provider half): while /me is in flight the provider reports profileLoading", async () => {
    getMe.mockReturnValue(pending<Profile>())

    const { getByTestId } = renderProvider()
    await flush()

    // ProtectedRoute cannot render a waiting treatment for a state nobody publishes.
    expect(getByTestId('profile-loading').textContent).toBe('true')
    expect(getByTestId('profile-error').textContent).toBe('false')
  })
})
