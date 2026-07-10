import { useContext } from 'react'
import { AuthContext, type AuthContextValue } from './context'

// Re-exported so pages import the gate logic and the hook from one place.
export { toSnapshot } from './destination'

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>')
  return value
}
