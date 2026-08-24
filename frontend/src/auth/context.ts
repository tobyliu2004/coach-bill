import { createContext } from 'react'
import type { Session } from '@supabase/supabase-js'
import type { Profile } from '../lib/api'
import type { AuthStatus } from './destination'

export interface AuthContextValue {
  status: AuthStatus
  session: Session | null
  profile: Profile | null
  /** True when the profile fetch failed for a non-auth reason (retry via refreshProfile). */
  profileError: boolean
  /**
   * True while the profile fetch is actually in flight.
   *
   * Published explicitly rather than inferred from `profile === null`, because "we are still
   * asking" and "we asked and it failed" are different answers and #46 happened by conflating
   * them. A screen that cannot name the third state renders it as one of the other two.
   */
  profileLoading: boolean
  refreshProfile: () => Promise<void>
  signOut: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)
