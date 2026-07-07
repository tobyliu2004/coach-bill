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

  // e.g. expired or already-used links: Supabase redirects with error params.
  const linkError = params.get('error_description')

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

  return (
    <main className="flex min-h-dvh items-center justify-center px-6">
      <p className="font-mono text-sm text-fg-muted">Signing you in…</p>
    </main>
  )
}

export default AuthCallback
