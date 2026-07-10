/**
 * Owns the auth state for everything behind the AuthLayout chunk: the supabase session
 * and the backend profile (which drives the onboarding gate). Pages read it via useAuth.
 */
import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import type { Session } from '@supabase/supabase-js'
import { ApiAuthError, type Profile } from '../lib/api'
import { api } from '../lib/client'
import { supabase } from '../lib/supabase'
import { AuthContext } from './context'
import type { AuthStatus } from './destination'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('loading')
  const [session, setSession] = useState<Session | null>(null)
  const [profile, setProfile] = useState<Profile | null>(null)
  const [profileError, setProfileError] = useState(false)

  useEffect(() => {
    // getSession resolves from localStorage (fast); onAuthStateChange keeps us live
    // afterwards (sign-in, sign-out, token refresh, the OAuth/email-link callback).
    let mounted = true
    void supabase.auth.getSession().then(({ data }) => {
      if (!mounted) return
      setSession(data.session)
      setStatus(data.session ? 'signedIn' : 'signedOut')
    })
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next)
      setStatus(next ? 'signedIn' : 'signedOut')
      if (!next) {
        setProfile(null)
        setProfileError(false)
      }
    })
    return () => {
      mounted = false
      subscription.unsubscribe()
    }
  }, [])

  const refreshProfile = useCallback(async () => {
    setProfileError(false)
    try {
      setProfile(await api.getMe())
    } catch (err) {
      if (err instanceof ApiAuthError) {
        // Token is unusable — treat as signed out rather than looping on 401s.
        await supabase.auth.signOut()
        return
      }
      setProfileError(true)
    }
  }, [])

  // First profile load after sign-in (the onboarding gate needs it).
  useEffect(() => {
    if (status === 'signedIn' && profile === null && !profileError) void refreshProfile()
  }, [status, profile, profileError, refreshProfile])

  const signOut = useCallback(async () => {
    await supabase.auth.signOut()
  }, [])

  return (
    <AuthContext.Provider
      value={{ status, session, profile, profileError, refreshProfile, signOut }}
    >
      {children}
    </AuthContext.Provider>
  )
}
