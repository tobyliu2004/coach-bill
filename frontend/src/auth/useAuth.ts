import { useContext } from 'react'
import { AuthContext, type AuthContextValue } from './context'
import type { AuthSnapshot } from './destination'

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>')
  return value
}

/** The shape resolveDestination consumes. Onboarding is complete once a goal is set. */
export function toSnapshot(auth: Pick<AuthContextValue, 'status' | 'profile'>): AuthSnapshot {
  return {
    status: auth.status,
    onboarded: auth.profile ? auth.profile.goal !== null : null,
  }
}
