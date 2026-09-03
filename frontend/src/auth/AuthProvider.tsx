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
   * The open `/me`, shared by every concurrent caller — refs, not state, because both have to
   * be readable and writable synchronously inside one event; state updates are batched and
   * would not be visible to the caller in time.
   *
   * Holding the PROMISE rather than a boolean matters: a caller that arrives while a request
   * is open gets the real request to await, instead of a bail that resolves instantly and
   * lies about having refreshed.
   */
  const inFlight = useRef<Promise<void> | null>(null)
  /**
   * Bumped on every auth transition. A `/me` answer from an older generation was issued for a
   * previous session and is discarded rather than applied — see the guard in `refreshProfile`.
   */
  const generation = useRef(0)

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
      // ANY auth transition invalidates a `/me` that is already open: it was issued for the
      // previous session, so its answer describes the previous user.
      //
      // Clearing `inFlight` here is not tidiness, it is the fix for a cross-user leak. A
      // request left open across a sign-out kept the ref set, so the NEXT user's sign-in hit
      // the "one at a time" bail and issued no request of its own — then the first user's
      // answer landed and painted their name, goal and onboarding verdict onto the second
      // user's session, permanently, because the auto-fetch effect only fires on a null
      // profile. The generation guard below stops the stale answer from being applied; this
      // line stops the new session from being starved of a request.
      generation.current += 1
      inFlight.current = null
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

  const refreshProfile = useCallback((): Promise<void> => {
    // One request at a time, shared: StrictMode's double-invoked effect, the auto-fetch, and a
    // fast double-click on "Try again" all join the same open request rather than opening
    // their own — and all get a promise that actually resolves when the answer lands.
    const open = inFlight.current
    if (open) return open

    const gen = generation.current
    setProfileLoading(true)

    const run = async (): Promise<void> => {
      try {
        const me = await api.getMe()
        // The session changed while this was open, so this answer is about someone else.
        if (gen !== generation.current) return
        setProfile(me)
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
        if (gen !== generation.current) return
        if (err instanceof ApiAuthError) {
          // Token is unusable — treat as signed out rather than looping on 401s.
          await supabase.auth.signOut()
          return
        }
        setProfileError(true)
      } finally {
        // Only the CURRENT generation owns the loading flag; an older request clearing it would
        // wipe the newer session's in-flight state.
        if (gen === generation.current) setProfileLoading(false)
      }
    }

    const pending = run().finally(() => {
      // Never clear a NEWER request's slot — an auth transition may already have replaced it.
      if (inFlight.current === pending) inFlight.current = null
    })
    inFlight.current = pending
    return pending
  }, [])

  // First profile load after sign-in (the onboarding gate needs it).
  useEffect(() => {
    if (status === 'signedIn' && profile === null && !profileError)
      void refreshProfile()
  }, [status, profile, profileError, refreshProfile])

  const signOut = useCallback(async () => {
    await supabase.auth.signOut()
  }, [])

  return (
    <AuthContext.Provider
      value={{
        status,
        session,
        profile,
        profileError,
        profileLoading,
        refreshProfile,
        signOut,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}
