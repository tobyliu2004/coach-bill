/**
 * The auth redirect state machine, specified before implementation.
 *
 * Every guard decision in the app funnels through resolveDestination so it can be
 * tested exhaustively here — the redirect logic is the most bug-prone part of any
 * auth UI (loops, flashes, dead ends).
 */
import { describe, expect, it } from 'vitest'
import { resolveDestination, toSnapshot, type AuthSnapshot } from './destination'

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

describe('toSnapshot — what counts as onboarded', () => {
  it('requires BOTH a goal and recorded consent', () => {
    const base = { status: 'signedIn' as const }
    const snap = (goal: string | null, consented_at: string | null) =>
      toSnapshot({ ...base, profile: { goal, consented_at } }).onboarded

    expect(snap('cut to 175', '2026-07-08T00:00:00Z')).toBe(true)
    // A goal without recorded consent must NOT open the app.
    expect(snap('cut to 175', null)).toBe(false)
    expect(snap(null, '2026-07-08T00:00:00Z')).toBe(false)
    expect(snap(null, null)).toBe(false)
  })

  it('is unknown (null) while the profile has not loaded', () => {
    expect(toSnapshot({ status: 'signedIn', profile: null }).onboarded).toBeNull()
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

// --- /history is a protected in-app path (issue #20, PR 1 — rows 17-20) ---
//
// Appended, not edited: every test above is #17's oracle and stays byte-identical. The
// four rows below are one guard each on the same terminal line, which today reads
// `return path === '/app' ? null : '/app'` — the line the build has to teach about a
// SECOND in-app path without breaking the first.

describe('/history as a destination', () => {
  // AC row 17: an onboarded user on /history stays put. Without this the new screen is
  // literally unreachable — the terminal line bounces everything that isn't '/app'.
  it('lets an onboarded user stay on /history', () => {
    expect(resolveDestination(onboarded, '/history')).toBeNull()
  })

  // AC row 18: a signed-out visitor to /history goes to /login. A protected screen that
  // does not redirect signed-out users is a hole, not a convenience.
  it('sends a signed-out visitor from /history to /login', () => {
    expect(resolveDestination(signedOut, '/history')).toBe('/login')
  })

  // AC row 19: an un-onboarded user goes to /onboarding. They have no timezone yet, so
  // their history would be computed in UTC — the #18 bug arriving through the front door.
  it('funnels a not-yet-onboarded user from /history to /onboarding', () => {
    expect(resolveDestination(fresh, '/history')).toBe('/onboarding')
  })

  // AC row 20 (REGRESSION GUARD): /app still stays put once /history exists. Rows 17 and
  // 20 together are what stop the fix for one path from inverting the other.
  it('still leaves an onboarded user alone on /app', () => {
    expect(resolveDestination(onboarded, '/app')).toBeNull()
  })
})

// --- /trends is the THIRD protected in-app path (issue #20, PR 2 — row 35) ---
//
// Appended, not edited: every test above is #17's and PR 1's oracle and stays byte-identical.
// Row 35 mirrors PR 1's rows 17-20 one path further on, and it is the same terminal line
// being taught a third in-app route. The regression guards matter more each time: the more
// paths that line has to know about, the easier it is to fix one by breaking another.

describe('/trends as a destination', () => {
  // AC row 35: an onboarded user on /trends stays put. Without this the dashboard is
  // literally unreachable — the terminal line bounces everything it does not recognise.
  it('lets an onboarded user stay on /trends', () => {
    expect(resolveDestination(onboarded, '/trends')).toBeNull()
  })

  // AC row 35: a signed-out visitor to /trends goes to /login. A screen that renders a
  // user's tonnage, weight and calories must not be reachable without a session.
  it('sends a signed-out visitor from /trends to /login', () => {
    expect(resolveDestination(signedOut, '/trends')).toBe('/login')
  })

  // AC row 35: an un-onboarded user goes to /onboarding. They have no timezone yet, so
  // every window on this screen would be computed in UTC — #18's bug through the front door.
  it('funnels a not-yet-onboarded user from /trends to /onboarding', () => {
    expect(resolveDestination(fresh, '/trends')).toBe('/onboarding')
  })

  // AC row 35 (REGRESSION GUARD, explicitly named by the row): /app is unchanged. This is
  // the assertion that fails if /trends is added by rewriting the terminal line rather than
  // extending it.
  it('still leaves an onboarded user alone on /app', () => {
    expect(resolveDestination(onboarded, '/app')).toBeNull()
  })

  // AC row 35 (the other regression guard the row implies): /history — PR 1's path — is
  // still reachable once /trends exists. Two in-app paths were already a set; three is a
  // list someone can truncate.
  it('still leaves an onboarded user alone on /history', () => {
    expect(resolveDestination(onboarded, '/history')).toBeNull()
  })
})
