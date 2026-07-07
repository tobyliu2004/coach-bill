/**
 * The auth redirect state machine — every guard decision funnels through here.
 *
 * Pure function so it's exhaustively unit-tested (destination.test.ts). Consumers
 * (ProtectedRoute, Login, AuthCallback) just render a <Navigate> to whatever this says.
 */

export type AuthStatus = 'loading' | 'signedOut' | 'signedIn'

export interface AuthSnapshot {
  status: AuthStatus
  /** Has the user finished onboarding (profile.goal set)? null = profile not loaded yet. */
  onboarded: boolean | null
}

const PROTECTED_PATHS = ['/app', '/onboarding']

/** Where the user must be sent, or null to stay put. */
export function resolveDestination(auth: AuthSnapshot, path: string): string | null {
  // Nothing is known yet — never redirect on a guess.
  if (auth.status === 'loading') return null

  if (auth.status === 'signedOut') {
    return PROTECTED_PATHS.includes(path) ? '/login' : null
  }

  // Signed in. A recovery-link session must always be able to set a new password.
  if (path === '/auth/reset') return null

  // Profile fetch still in flight — hold position until we know where they belong.
  if (auth.onboarded === null) return null

  if (!auth.onboarded) {
    return path === '/onboarding' ? null : '/onboarding'
  }

  // Onboarded: the auth pages and onboarding no longer apply.
  return path === '/app' ? null : '/app'
}
