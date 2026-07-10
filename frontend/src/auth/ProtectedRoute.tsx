import { Navigate, Outlet, useLocation } from 'react-router'
import { toSnapshot, useAuth } from './useAuth'
import { resolveDestination } from './destination'

/** Route guard: renders the state machine's verdict (redirect, wait, or proceed). */
export function ProtectedRoute() {
  const auth = useAuth()
  const location = useLocation()

  const destination = resolveDestination(toSnapshot(auth), location.pathname)
  if (destination) return <Navigate to={destination} replace />

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

  // Session or profile still resolving — a sub-100ms blank beats a spinner flash.
  if (auth.status === 'loading' || (auth.status === 'signedIn' && auth.profile === null)) {
    return null
  }

  return <Outlet />
}
