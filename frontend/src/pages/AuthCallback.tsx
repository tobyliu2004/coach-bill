import { useEffect, useState } from 'react'
import { Link, Navigate, useLocation, useSearchParams } from 'react-router'
import { resolveDestination } from '../auth/destination'
import { toSnapshot, useAuth } from '../auth/useAuth'

/**
 * Where email-verification links and the Google OAuth redirect land. supabase-js
 * exchanges the ?code= automatically (PKCE, detectSessionInUrl); this page just waits
 * for the session to appear, then lets the state machine route onward. It must never
 * hang forever — a dead link gets an explanation and a way back.
 */
function AuthCallback() {
  const auth = useAuth()
  const location = useLocation()
  const [params] = useSearchParams()
  const [timedOut, setTimedOut] = useState(false)

  // e.g. expired or already-used links: Supabase redirects with error params — usually
  // in the query string, but some flows deliver them in the URL fragment instead.
  const linkError =
    params.get('error_description') ??
    new URLSearchParams(window.location.hash.slice(1)).get('error_description')

  useEffect(() => {
    const timer = setTimeout(() => setTimedOut(true), 6000)
    return () => clearTimeout(timer)
  }, [])

  const destination = resolveDestination(toSnapshot(auth), location.pathname)
  if (destination) return <Navigate to={destination} replace />

  if (linkError || (timedOut && auth.status !== 'signedIn')) {
    return (
      <main className="flex min-h-dvh flex-col items-center justify-center gap-4 px-6 text-center">
        <p className="max-w-sm text-sm leading-relaxed text-fg-muted">
          {linkError
            ? `That link didn't work: ${linkError}.`
            : 'That link didn’t sign you in — it may have expired or been used already.'}
        </p>
        <Link
          to="/login"
          className="rounded-control border border-edge-strong px-4 py-2 text-sm font-semibold text-fg transition-colors duration-150 hover:border-fg-muted"
        >
          Back to sign in
        </Link>
      </main>
    )
  }

  // Signed in, but the profile fetch failed for a non-auth reason.
  //
  // THIS IS THE #46 DEAD END. The session is fine — it is the profile that failed — so
  // `status` is 'signedIn' and the 6s escape above, gated on `status !== 'signedIn'`, is
  // disabled by the very condition that caused the wait. `resolveDestination` correctly
  // returns null (profile null means "hold position until we know where they belong"), and
  // nothing rendered `profileError` on this path, so the user sat on "Signing you in…"
  // forever with no error, no retry and no way back. Every piece was defensible; the
  // composition was a trap.
  //
  // Ordered AFTER the link-error branch on purpose: an expired link explains itself better
  // than a generic profile failure, and #46 row 6 pins that precedence.
  // THE RETRY STAYS ON THIS SCREEN, and that is the whole point of the branch.
  //
  // An earlier cut gated this on `!auth.profileLoading`, so pressing "Try again" fell through
  // to the bare "Signing you in…" line below — which has no button and no link. `getMe` has no
  // `AbortSignal.timeout` (only `requestReply` does), so against a hung socket that promise
  // never settles and the 6s escape is disabled by `status !== 'signedIn'`. The button added
  // to fix a dead end led straight into another one. Both PR reviewers caught it independently.
  //
  // Feedback comes from the CONTROL instead: the button disables and relabels while the
  // request is open, and "Back to sign in" never leaves the screen. Approved row 10 — "there
  // is always a way out" — has to hold in the in-flight state too, not just the settled one.
  if (auth.status === 'signedIn' && auth.profileError) {
    return (
      <main className="flex min-h-dvh flex-col items-center justify-center gap-4 px-6 text-center">
        <p role="alert" className="max-w-sm text-sm leading-relaxed text-fg-muted">
          You&rsquo;re signed in, but we couldn&rsquo;t load your profile.
        </p>
        <div className="flex items-center gap-3">
          <button
            type="button"
            disabled={auth.profileLoading}
            onClick={() => void auth.refreshProfile()}
            className="rounded-control border border-edge-strong px-4 py-2 text-sm font-semibold text-fg transition-colors duration-150 hover:border-fg-muted disabled:opacity-50 disabled:hover:border-edge-strong"
          >
            {auth.profileLoading ? 'Trying…' : 'Try again'}
          </button>
          {/* The second way out, and it is not decoration: if retrying keeps failing, the
              only remaining exit used to be editing the URL by hand. */}
          <Link
            to="/login"
            className="rounded-control px-4 py-2 text-sm text-fg-muted transition-colors duration-150 hover:text-fg"
          >
            Back to sign in
          </Link>
        </div>
      </main>
    )
  }

  return (
    <main className="flex min-h-dvh items-center justify-center px-6">
      <p className="font-mono text-sm text-fg-muted">Signing you in…</p>
    </main>
  )
}

export default AuthCallback
