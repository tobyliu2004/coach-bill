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
   * Bumped when the SIGNED-IN IDENTITY changes. A `/me` answer from an older generation was
   * issued for a previous user and is discarded rather than applied — see the guard in
   * `refreshProfile`.
   */
  const generation = useRef(0)
  /**
   * Who the current state describes, held twice on purpose:
   *   - the ref, because `applySession` has to compare against it synchronously inside one
   *     event, before any state update has been committed;
   *   - the state, because the auto-fetch effect has to RE-RUN when it changes. A request that
   *     gets discarded and re-issued by nobody is the dead end this whole issue is about.
   */
  const currentUserId = useRef<string | null>(null)
  const [userId, setUserId] = useState<string | null>(null)

  /**
   * Fold a session into state. Both entry points come through here — `getSession()` on mount
   * and every `onAuthStateChange` event — so "what invalidates an open `/me`" is decided in
   * exactly one place.
   *
   * IDENTITY, NOT EVENT COUNT (#46 rows 11-13). This used to bump `generation` on EVERY auth
   * event, which was wrong in both directions at once:
   *
   *  - Too eager. supabase-js re-emits `TOKEN_REFRESHED`/`SIGNED_IN` for the SAME user on every
   *    hidden->visible transition (`GoTrueClient._onVisibilityChanged` -> `_recoverAndRefresh`).
   *    So: cold backend, `/me` hangs, the user tabs away and back — the likeliest thing to do
   *    during a long wait — and the bump threw away their own answer. The generation-gated
   *    `finally` then left `profileLoading` stuck `true`, and no dep of the auto-fetch effect
   *    had changed (`setStatus('signedIn')` on an already-`signedIn` value is a React bail-out),
   *    so nothing ever asked again. Terminal state, on a branch that deliberately has no
   *    controls: #46 reproduced by #46's own fix. Same race on first load between `getSession()`
   *    and `INITIAL_SESSION`.
   *  - Too lax. A direct A->B change with no `SIGNED_OUT` between kept A's profile on screen for
   *    B, and the auto-fetch only fires on a null profile, so B's was never requested at all.
   *
   * Comparing the user id fixes both: a same-user event leaves the open request alone, and a
   * real identity change clears the stale profile AND re-arms the fetch via `userId`.
   */
  const applySession = useCallback((next: Session | null) => {
    setSession(next)
    setStatus(next ? 'signedIn' : 'signedOut')

    const nextUserId = next?.user.id ?? null
    // Same person — a token refresh, or the SIGNED_IN supabase-js re-emits on tab focus.
    // Nothing about the profile has been invalidated, so touch nothing.
    if (nextUserId === currentUserId.current) return

    currentUserId.current = nextUserId
    // Anything still in the air was issued for the PREVIOUS user: discard its answer (the guard
    // in `refreshProfile`) and free the slot, so the new session is not starved of a request of
    // its own by the "one at a time" bail.
    generation.current += 1
    inFlight.current = null
    setUserId(nextUserId)
    // Clearing `profile` is the cross-user leak fix, not tidiness: the auto-fetch below only
    // fires on a null profile, so leaving A's row here both shows it to B and prevents the
    // request that would replace it.
    setProfile(null)
    setProfileError(false)
    setProfileLoading(false)
  }, [])

  useEffect(() => {
    // getSession resolves from localStorage (fast); onAuthStateChange keeps us live
    // afterwards (sign-in, sign-out, token refresh, the OAuth/email-link callback).
    let mounted = true
    void supabase.auth.getSession().then(({ data }) => {
      if (!mounted) return
      applySession(data.session)
    })
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, next) => {
      applySession(next)
    })
    return () => {
      mounted = false
      subscription.unsubscribe()
    }
  }, [applySession])

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

  // First profile load after sign-in, and again whenever the signed-in identity changes.
  //
  // `userId` rather than `status` is the trigger, and that is the fix for the re-arm half of
  // rows 11-13: `status` is `'signedIn'` both before and after an A->B switch, so an effect
  // keyed on it never re-runs and the new user is never fetched. `userId !== null` is
  // equivalent to `status === 'signedIn'` (both are derived from the same session) while
  // additionally changing when the person does.
  useEffect(() => {
    if (userId !== null && profile === null && !profileError) void refreshProfile()
  }, [userId, profile, profileError, refreshProfile])

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
