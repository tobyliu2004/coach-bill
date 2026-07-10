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
  refreshProfile: () => Promise<void>
  signOut: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)
