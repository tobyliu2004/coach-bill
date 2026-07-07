/**
 * The auth redirect state machine, specified before implementation.
 *
 * Every guard decision in the app funnels through resolveDestination so it can be
 * tested exhaustively here — the redirect logic is the most bug-prone part of any
 * auth UI (loops, flashes, dead ends).
 */
import { describe, expect, it } from 'vitest'
import { resolveDestination, type AuthSnapshot } from './destination'

const loading: AuthSnapshot = { status: 'loading', onboarded: null }
const signedOut: AuthSnapshot = { status: 'signedOut', onboarded: null }
const fresh: AuthSnapshot = { status: 'signedIn', onboarded: false } // no goal yet
const onboarded: AuthSnapshot = { status: 'signedIn', onboarded: true }
const profilePending: AuthSnapshot = { status: 'signedIn', onboarded: null } // /me in flight

describe('while auth state is loading', () => {
  it.each(['/login', '/auth/callback', '/onboarding', '/app'])('stays put on %s', (path) => {
    expect(resolveDestination(loading, path)).toBeNull()
  })
})

describe('signed out', () => {
  it('is sent to /login from protected pages', () => {
    expect(resolveDestination(signedOut, '/app')).toBe('/login')
    expect(resolveDestination(signedOut, '/onboarding')).toBe('/login')
  })

  it('may visit the auth pages', () => {
    expect(resolveDestination(signedOut, '/login')).toBeNull()
    expect(resolveDestination(signedOut, '/auth/callback')).toBeNull()
    expect(resolveDestination(signedOut, '/auth/reset')).toBeNull()
  })
})

describe('signed in, profile still being fetched', () => {
  it.each(['/login', '/auth/callback', '/onboarding', '/app'])(
    'waits (no redirect) on %s',
    (path) => {
      expect(resolveDestination(profilePending, path)).toBeNull()
    },
  )
})

describe('signed in, not yet onboarded (goal unset)', () => {
  it('is funneled to /onboarding from everywhere', () => {
    expect(resolveDestination(fresh, '/app')).toBe('/onboarding')
    expect(resolveDestination(fresh, '/login')).toBe('/onboarding')
    expect(resolveDestination(fresh, '/auth/callback')).toBe('/onboarding')
  })

  it('stays on /onboarding once there', () => {
    expect(resolveDestination(fresh, '/onboarding')).toBeNull()
  })

  it('may still use the password-reset page', () => {
    // A recovery-link session must be able to set a new password before anything else.
    expect(resolveDestination(fresh, '/auth/reset')).toBeNull()
  })
})

describe('signed in and onboarded', () => {
  it('is in the app and stays there', () => {
    expect(resolveDestination(onboarded, '/app')).toBeNull()
  })

  it('is bounced from pages that no longer apply', () => {
    expect(resolveDestination(onboarded, '/login')).toBe('/app')
    expect(resolveDestination(onboarded, '/onboarding')).toBe('/app')
    expect(resolveDestination(onboarded, '/auth/callback')).toBe('/app')
  })

  it('may still use the password-reset page', () => {
    expect(resolveDestination(onboarded, '/auth/reset')).toBeNull()
  })
})
