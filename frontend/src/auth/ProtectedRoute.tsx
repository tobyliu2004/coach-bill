import { Link, Navigate, Outlet, useLocation } from 'react-router'
import { toSnapshot, useAuth } from './useAuth'
import { resolveDestination } from './destination'

/** Route guard: renders the state machine's verdict (redirect, wait, or proceed). */
export function ProtectedRoute() {
  const auth = useAuth()
  const location = useLocation()

  const destination = resolveDestination(toSnapshot(auth), location.pathname)
  if (destination) return <Navigate to={destination} replace />

  // Session or profile still resolving.
  //
  // This used to `return null`, commented "a sub-100ms blank beats a spinner flash". That is
  // true against a warm backend and false against a cold one: Render's free tier cold-starts,
  // so the worst case was a blank white page for tens of seconds with nothing to tell the user
  // whether the app was loading or broken. Same bug family as the rest of #46 — an in-flight
  // state rendered as something else.
  //
  // `auth.profile === null` GATES THE WHOLE CLAUSE, and that is not incidental: this screen is
  // only correct when there is nothing to render from. `profileLoading` on its own would also
  // fire for a REFRESH of an already-loaded profile — `Onboarding.tsx:42` does exactly that
  // after submitting — and would replace the live screen with a loading page mid-submit,
  // unmounting the form. Blank the app only when the alternative is a blank app.
  //
  // `!profileError` rather than a `profileLoading` test, because a retry must NOT land here:
  // this screen has no controls, so a hung retry on it would be a dead end. A failed attempt
  // keeps the error branch below, which carries the in-flight state on its button instead.
  if (
    auth.status === 'loading' ||
    (auth.status === 'signedIn' && auth.profile === null && !auth.profileError)
  ) {
    return (
      <main className="flex min-h-dvh items-center justify-center px-6">
        {/* Session-neutral while `status === 'loading'`: getSession() has not resolved, so we
            do not yet know there IS a profile to load, and claiming one would be a small lie
            on the screen a signed-out user sees for a frame before the /login redirect. */}
        <p className="font-mono text-sm text-fg-muted">
          {auth.status === 'loading' ? 'Loading…' : 'Loading your profile…'}
        </p>
      </main>
    )
  }

  // `auth.profile === null` gates this the same way it gates the branch above, and the
  // symmetry is the rule: BLANK THE APP ONLY WHEN THE ALTERNATIVE IS A BLANK APP. A refresh
  // of an already-loaded profile can fail — `Onboarding.tsx:42` refreshes right after a
  // successful save — and replacing a working screen with a full-page error would throw away
  // usable state the user is in the middle of, over a failure that cost us nothing.
  //
  // The retry stays HERE rather than falling through to the waiting screen: that screen has no
  // controls, and `getMe` has no timeout, so a hung retry there would be a dead end. The button
  // carries the in-flight state instead, and the way out never leaves the screen.
  if (auth.profile === null && auth.profileError) {
    return (
      <main className="flex min-h-dvh flex-col items-center justify-center gap-4 px-6">
        <p className="text-sm text-fg-muted">Couldn&rsquo;t load your profile.</p>
        <div className="flex items-center gap-3">
          <button
            type="button"
            disabled={auth.profileLoading}
            onClick={() => void auth.refreshProfile()}
            className="rounded-control border border-edge-strong px-4 py-2 text-sm font-semibold text-fg transition-colors duration-150 hover:border-fg-muted disabled:opacity-50 disabled:hover:border-edge-strong"
          >
            {auth.profileLoading ? 'Trying…' : 'Try again'}
          </button>
          {/* The second exit. With one control that disables while retrying, a hung `/me`
              would leave this screen with nothing enabled on it — a dead end reached from
              the dead-end fix. */}
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

  return <Outlet />
}
