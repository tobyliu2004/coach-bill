/**
 * Oracle suite for issue #46 — the APPROVED AMENDMENT, rows 11, 12 and 13.
 *
 * DIRECTION: NARROWING (all of rows 11-15, as approved on issue #46 on 2026-09-03). Each row
 * adds a case that can only newly FAIL; none can make a previously-failing case pass. Every
 * test here is written before the fix exists and must be RED against the current
 * `AuthProvider.tsx`.
 *
 * WHY THIS FILE EXISTS AT ALL, rather than more tests in `AuthProvider.test.tsx`: the
 * session-invalidation logic in `d69c46f` (the `generation` ref, the `inFlight` clear, the
 * generation-gated `finally`) is executed by NO test in the repo, because
 * `AuthProvider.test.tsx:24` mocks `onAuthStateChange` as a `vi.fn()` whose callback is never
 * invoked. Nothing can fire an auth event, so nothing can reach that code.
 *
 * THE MOCK CAPABILITY THIS ADDS, called out because it is the only reason these rows are
 * testable: the `onAuthStateChange` mock below CAPTURES the callback the provider registers
 * and hands the test a way to invoke it. That is strictly more capability than the existing
 * mock, never less — no existing assertion is changed, weakened or skipped, and the existing
 * suite keeps its own non-firing mock in its own file.
 *
 * All three rows have the same shape: a `/me` answer that is still in the air when an auth
 * event lands. `deferred()` (harness) lets the test own the moment that answer arrives, so
 * "the answer landed AFTER the event" is arranged rather than raced.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render } from '@testing-library/react'
import { useEffect } from 'react'
import type { AuthChangeEvent, Session } from '@supabase/supabase-js'
import { ApiError, type Profile } from '../lib/api'
import { aProfile, aSession, deferred, flush } from '../test/harness'

type AuthChangeHandler = (event: AuthChangeEvent, session: Session | null) => void

/**
 * Hoisted so the `vi.mock` factory (which runs before the imports above) can reach it, and
 * mutable so each test decides what `getSession()` answers and can fire auth events itself.
 */
const authMock = vi.hoisted(() => {
  const listeners: AuthChangeHandler[] = []
  const state: { session: Session | null } = { session: null }
  return { listeners, state }
})

vi.mock('../lib/supabase', () => ({
  supabase: {
    auth: {
      getSession: vi.fn(() => Promise.resolve({ data: { session: authMock.state.session } })),
      onAuthStateChange: (callback: AuthChangeHandler) => {
        authMock.listeners.push(callback)
        return {
          data: {
            subscription: {
              unsubscribe: () => {
                const at = authMock.listeners.indexOf(callback)
                if (at >= 0) authMock.listeners.splice(at, 1)
              },
            },
          },
        }
      },
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
import { AuthProvider } from './AuthProvider'
import { useAuth } from './useAuth'

const getMe = vi.mocked(api.getMe)

/** Two users whose profiles share no field value, so "whose data is on screen" is decidable. */
const USER_A = 'oracle-user-a'
const USER_B = 'oracle-user-b'
const PROFILE_A = aProfile({
  id: 'profile-a',
  display_name: 'oracle-name-a',
  goal: 'oracle-goal-a',
  weight_unit: 'lb',
})
const PROFILE_B = aProfile({
  id: 'profile-b',
  display_name: 'oracle-name-b',
  goal: 'oracle-goal-b',
  weight_unit: 'kg',
})

/**
 * Every committed render's profile, as one string per commit.
 *
 * "The previous user's name/goal/unit is never rendered" (row 12) is a statement about every
 * frame, not about the last one — a stale answer that lands and is corrected a tick later
 * still leaked. `useEffect` with no dependency array runs after EVERY commit, so this records
 * the whole sequence rather than a snapshot.
 */
const rendered: string[] = []

function signatureOf(profile: Profile | null): string {
  if (profile === null) return 'none'
  return `${profile.display_name ?? ''}|${profile.goal ?? ''}|${profile.weight_unit}`
}

/** Renders the context out as DATA, so no assertion here depends on any screen's markup. */
function Probe() {
  const auth = useAuth()
  const signature = signatureOf(auth.profile)
  useEffect(() => {
    rendered.push(signature)
  })
  return (
    <div>
      <span data-testid="status">{auth.status}</span>
      <span data-testid="profile">{signature}</span>
      <span data-testid="profile-error">{String(auth.profileError)}</span>
      <span data-testid="profile-loading">{String(auth.profileLoading)}</span>
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

/** Fire a Supabase auth event at every listener the provider registered. */
async function emitAuth(event: AuthChangeEvent, session: Session | null): Promise<void> {
  await act(async () => {
    for (const listener of [...authMock.listeners]) listener(event, session)
    await Promise.resolve()
  })
}

/** Settle an in-flight `/me` inside `act`, so the resulting render is flushed. */
async function land(settle: () => void): Promise<void> {
  await act(async () => {
    settle()
    await Promise.resolve()
  })
  await flush()
}

beforeEach(() => {
  authMock.listeners.length = 0
  authMock.state.session = aSession(USER_A)
  rendered.length = 0
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('#46 row 11 — an auth event for the SAME user while /me is in flight', () => {
  it('row 11: the in-flight answer is still applied; profileLoading returns to false', async () => {
    const me = deferred<Profile>()
    // Every call returns the same answer, so the row is not accidentally also asserting
    // "the provider must not re-fetch" — either way the answer for this user must land.
    getMe.mockReturnValue(me.promise)

    const { getByTestId } = renderProvider()
    await flush()
    // Precondition, not the row: we are genuinely in the in-flight state before the event.
    expect(getByTestId('profile-loading').textContent).toBe('true')

    // A token refresh / tab re-focus for the SAME user.
    await emitAuth('TOKEN_REFRESHED', aSession(USER_A))
    await land(() => {
      me.resolve(PROFILE_A)
    })

    expect(getByTestId('profile').textContent).toBe(signatureOf(PROFILE_A))
    // The negative half of the row: NOT stuck on "Loading your profile…".
    expect(getByTestId('profile-loading').textContent).toBe('false')
    expect(getByTestId('profile-error').textContent).toBe('false')
  })

  it('row 11: the same holds for a repeated SIGNED_IN for the same user', async () => {
    const me = deferred<Profile>()
    getMe.mockReturnValue(me.promise)

    const { getByTestId } = renderProvider()
    await flush()
    expect(getByTestId('profile-loading').textContent).toBe('true')

    await emitAuth('SIGNED_IN', aSession(USER_A))
    await land(() => {
      me.resolve(PROFILE_A)
    })

    expect(getByTestId('profile').textContent).toBe(signatureOf(PROFILE_A))
    expect(getByTestId('profile-loading').textContent).toBe('false')
  })
})

describe('#46 row 12 — an auth event for a DIFFERENT user while /me is in flight', () => {
  it('row 12: the stale answer is never applied AND a fresh /me is issued for the new user', async () => {
    const meA = deferred<Profile>()
    const meB = deferred<Profile>()
    getMe.mockReturnValue(meB.promise)
    getMe.mockReturnValueOnce(meA.promise)

    const { getByTestId } = renderProvider()
    await flush()
    const callsBefore = getMe.mock.calls.length

    // User B signs in while user A's /me is still in the air.
    authMock.state.session = aSession(USER_B)
    await emitAuth('SIGNED_IN', aSession(USER_B))
    await flush()

    // Second half of the row: the new user's profile is actually asked for.
    expect(
      getMe.mock.calls.length - callsBefore,
      'an identity change must re-arm the profile fetch for the new user',
    ).toBeGreaterThanOrEqual(1)

    // A's answer arrives late.
    await land(() => {
      meA.resolve(PROFILE_A)
    })

    // The negative half of the row: A's name/goal/unit is never rendered, in ANY frame.
    expect(rendered).not.toContain(signatureOf(PROFILE_A))
    expect(getByTestId('profile').textContent).not.toBe(signatureOf(PROFILE_A))

    // B's answer arrives and is the one that lands.
    await land(() => {
      meB.resolve(PROFILE_B)
    })

    expect(rendered).not.toContain(signatureOf(PROFILE_A))
    expect(getByTestId('profile').textContent).toBe(signatureOf(PROFILE_B))
    expect(getByTestId('profile-loading').textContent).toBe('false')
  })
})

describe('#46 row 13 — a sign-out while /me is in flight', () => {
  it('row 13: profile, profileError and profileLoading are all cleared, and no stale answer lands', async () => {
    const first = deferred<Profile>()
    const retry = deferred<Profile>()
    getMe.mockReturnValue(retry.promise)
    getMe.mockReturnValueOnce(first.promise)

    const { getByRole, getByTestId } = renderProvider()
    await flush()
    // Arrange a state where all three fields have something to clear: a profile is loaded,
    // then a refresh is put in flight.
    await land(() => {
      first.resolve(PROFILE_A)
    })
    expect(getByTestId('profile').textContent).toBe(signatureOf(PROFILE_A))

    await act(async () => {
      fireEvent.click(getByRole('button', { name: /retry/i }))
    })
    await flush()
    expect(getByTestId('profile-loading').textContent).toBe('true')

    authMock.state.session = null
    await emitAuth('SIGNED_OUT', null)
    await flush()

    expect(getByTestId('profile').textContent).toBe('none')
    expect(getByTestId('profile-error').textContent).toBe('false')
    expect(getByTestId('profile-loading').textContent).toBe('false')

    // The negative half: the answer that was in the air when the user signed out never lands.
    await land(() => {
      retry.resolve(PROFILE_B)
    })

    expect(getByTestId('profile').textContent).toBe('none')
    expect(getByTestId('profile-error').textContent).toBe('false')
    expect(getByTestId('profile-loading').textContent).toBe('false')
    expect(rendered).not.toContain(signatureOf(PROFILE_B))
  })

  it('row 13: a sign-out during a retry clears profileError too', async () => {
    const retry = deferred<Profile>()
    getMe.mockReturnValue(retry.promise)
    getMe.mockRejectedValueOnce(new ApiError(500, '/me failed'))

    const { getByRole, getByTestId } = renderProvider()
    await flush()
    expect(getByTestId('profile-error').textContent).toBe('true')

    await act(async () => {
      fireEvent.click(getByRole('button', { name: /retry/i }))
    })
    await flush()

    authMock.state.session = null
    await emitAuth('SIGNED_OUT', null)
    await flush()

    expect(getByTestId('profile-error').textContent).toBe('false')
    expect(getByTestId('profile-loading').textContent).toBe('false')
    expect(getByTestId('profile').textContent).toBe('none')

    await land(() => {
      retry.resolve(PROFILE_A)
    })

    expect(getByTestId('profile').textContent).toBe('none')
    expect(getByTestId('profile-error').textContent).toBe('false')
  })
})

/**
 * The mock capability itself, proved in both directions — "a test that cannot fail is not a
 * test" applies to the instrument as much as to the assertion. If `emitAuth` fired at nobody
 * (the existing `vi.fn()` mock's behaviour), rows 11-13 would be three tests of nothing, and
 * they would be GREEN, because a provider that never sees an event never mishandles one.
 */
describe('#46 rows 11-13: the auth-event instrument itself', () => {
  it('the provider registers a listener, and emitAuth reaches it', async () => {
    getMe.mockReturnValue(deferred<Profile>().promise)
    renderProvider()
    await flush()

    expect(
      authMock.listeners.length,
      'AuthProvider must subscribe to onAuthStateChange for rows 11-13 to mean anything',
    ).toBeGreaterThan(0)

    const seen: AuthChangeEvent[] = []
    authMock.listeners.push((event) => seen.push(event))
    await emitAuth('TOKEN_REFRESHED', aSession(USER_A))
    expect(seen).toEqual(['TOKEN_REFRESHED'])
  })
})
