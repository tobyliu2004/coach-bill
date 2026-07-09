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

/** The profile fields the gate reads (structural so this module stays react-free). */
export interface ProfileGate {
  goal: string | null
  consented_at: string | null
}

/**
 * Onboarding is complete only when BOTH the goal is set and consent is recorded —
 * "in the app ⇒ consented" is enforced here, where the gate reads, not just by the
 * onboarding form that happens to write both today.
 */
export function toSnapshot(auth: {
  status: AuthStatus
  profile: ProfileGate | null
}): AuthSnapshot {
  return {
    status: auth.status,
    onboarded: auth.profile
      ? auth.profile.goal !== null && auth.profile.consented_at !== null
      : null,
  }
}

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
