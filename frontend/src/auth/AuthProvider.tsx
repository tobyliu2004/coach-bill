/**
 * Owns the auth state for everything behind the AuthLayout chunk: the supabase session
 * and the backend profile (which drives the onboarding gate). Pages read it via useAuth.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
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
  const [profileLoading, setProfileLoading] = useState(false)
  /**
   * A ref, not state, and deliberately: this guards against a SECOND request being started
   * while one is already open, and it has to be readable and writable synchronously within a
   * single event. State updates are batched and would not be visible to the caller in time.
   */
  const inFlight = useRef(false)

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
        setProfileLoading(false)
      }
    })
    return () => {
      mounted = false
      subscription.unsubscribe()
    }
  }, [])

  const refreshProfile = useCallback(async () => {
    // One request at a time. Without this, StrictMode's double-invoked effect (and a fast
    // double-click on "Try again") each open their own `/me`.
    if (inFlight.current) return
    inFlight.current = true
    setProfileLoading(true)
    try {
      setProfile(await api.getMe())
      // Cleared on SUCCESS, not on entry.
      //
      // To be precise about what fixes what, because the two are easy to confuse: the DOUBLE
      // FETCH (#46 row 8) is fixed by the `inFlight` ref above, not by this ordering — I
      // verified that by putting the entry-clear back and watching row 8 stay green. Do not
      // delete the ref on the strength of this comment.
      //
      // The ordering earns its place for a different reason: `profileError` is the answer to
      // "did the last completed attempt fail", and clearing it before a new attempt has
      // finished makes it briefly claim an outcome nobody has yet. The in-flight state is
      // published separately as `profileLoading`, so screens can say "we are asking again"
      // without anyone having to lie about the error first.
      setProfileError(false)
    } catch (err) {
      if (err instanceof ApiAuthError) {
        // Token is unusable — treat as signed out rather than looping on 401s.
        await supabase.auth.signOut()
        return
      }
      setProfileError(true)
    } finally {
      inFlight.current = false
      setProfileLoading(false)
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
      value={{ status, session, profile, profileError, profileLoading, refreshProfile, signOut }}
    >
      {children}
    </AuthContext.Provider>
  )
}
