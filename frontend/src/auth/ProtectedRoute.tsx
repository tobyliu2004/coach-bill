import { Navigate, Outlet, useLocation } from 'react-router'
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
  // Given no profile: `profileLoading` covers a retry that is in flight (where `profileError`
  // still describes the previous attempt), and `!profileError` covers the sliver before the
  // fetch effect has started, where nothing is in flight yet and there is still nothing to
  // show. Together they mean "no profile, and this is not a settled failure".
  if (
    auth.status === 'loading' ||
    (auth.status === 'signedIn' &&
      auth.profile === null &&
      (auth.profileLoading || !auth.profileError))
  ) {
    return (
      <main className="flex min-h-dvh items-center justify-center px-6">
        <p className="font-mono text-sm text-fg-muted">Loading your profile…</p>
      </main>
    )
  }

  // Deliberately AFTER the waiting branch, and the `!auth.profileError` clause above is what
  // stops the two from swallowing each other. `profileError` describes the last COMPLETED
  // attempt, so while a retry is open both conditions are true at once; keeping the failure
  // screen up for the whole retry would render the in-flight state as the failure state —
  // the bug this branch exists to kill, one level down. It is also what makes "Try again"
  // visibly do something on a cold backend, which is the case that produced #46.
  if (auth.profileError) {
    return (
      <main className="flex min-h-dvh flex-col items-center justify-center gap-4 px-6">
        <p className="text-sm text-fg-muted">Couldn&rsquo;t load your profile.</p>
        <button
          type="button"
          onClick={() => void auth.refreshProfile()}
          className="rounded-control border border-edge-strong px-4 py-2 text-sm font-semibold text-fg transition-colors duration-150 hover:border-fg-muted"
        >
          Try again
        </button>
      </main>
    )
  }

  return <Outlet />
}
