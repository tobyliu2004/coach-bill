import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, Navigate, useLocation, useSearchParams } from 'react-router'
import { resolveDestination } from '../auth/destination'
import { toSnapshot, useAuth } from '../auth/useAuth'
import { supabase } from '../lib/supabase'

type Mode = 'signin' | 'signup'

const inputClasses =
  'w-full rounded-control border border-edge-strong bg-bg px-3 py-2.5 text-sm text-fg ' +
  'placeholder:text-fg-muted focus:border-fg-muted focus:outline-none'

const primaryButtonClasses =
  'w-full rounded-control bg-accent px-5 py-2.5 text-sm font-semibold text-accent-ink ' +
  'transition-transform duration-150 ease-out-expo hover:scale-[1.02] active:scale-[0.98] ' +
  'disabled:opacity-50 disabled:hover:scale-100'

const secondaryButtonClasses =
  'w-full rounded-control border border-edge-strong px-5 py-2.5 text-sm font-semibold ' +
  'text-fg transition-colors duration-150 hover:border-fg-muted disabled:opacity-50'

function callbackUrl(): string {
  return `${window.location.origin}/auth/callback`
}

function Login() {
  const auth = useAuth()
  const location = useLocation()
  const [params] = useSearchParams()
  const [mode, setMode] = useState<Mode>(params.get('mode') === 'signup' ? 'signup' : 'signin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [needsConfirmation, setNeedsConfirmation] = useState(false)
  const [sentTo, setSentTo] = useState<string | null>(null)
  const [resent, setResent] = useState(false)
  const [resetSent, setResetSent] = useState(false)

  // Already signed in (or the moment sign-in succeeds): the state machine says where to go.
  const destination = resolveDestination(toSnapshot(auth), location.pathname)
  if (destination) return <Navigate to={destination} replace />

  // The not-medical-advice consent lives on the Onboarding screen — the one place every
  // account (email or Google, either entry mode) must pass before reaching /app.
  const signup = mode === 'signup'

  function switchMode(next: Mode) {
    setMode(next)
    setError(null)
    setNeedsConfirmation(false)
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setNeedsConfirmation(false)
    setBusy(true)
    try {
      if (signup) {
        const { data, error: signUpError } = await supabase.auth.signUp({
          email,
          password,
          options: { emailRedirectTo: callbackUrl() },
        })
        if (signUpError) {
          setError(signUpError.message)
        } else if (data.user && data.user.identities?.length === 0) {
          // Supabase's anti-enumeration response for an email that already has an account.
          setError('That email already has an account — sign in instead.')
        } else {
          setSentTo(email)
        }
      } else {
        const { error: signInError } = await supabase.auth.signInWithPassword({ email, password })
        if (signInError) {
          if (signInError.code === 'email_not_confirmed') {
            setNeedsConfirmation(true)
          } else {
            setError('Wrong email or password.')
          }
        }
        // Success needs no navigation here — the auth state change re-renders this
        // component and the <Navigate> at the top takes over.
      }
    } finally {
      setBusy(false)
    }
  }

  async function handleGoogle() {
    setError(null)
    setBusy(true)
    const { error: oauthError } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: callbackUrl() },
    })
    if (oauthError) {
      setError(oauthError.message)
      setBusy(false)
    }
    // On success the browser navigates away to Google.
  }

  async function handleForgotPassword() {
    if (!email) {
      setError('Enter your email above first, then tap “Forgot password?” again.')
      return
    }
    setError(null)
    await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: `${window.location.origin}/auth/reset`,
    })
    setResetSent(true)
  }

  async function handleResend(address: string) {
    setResent(false)
    await supabase.auth.resend({
      type: 'signup',
      email: address,
      options: { emailRedirectTo: callbackUrl() },
    })
    setResent(true)
  }

  return (
    <main className="flex min-h-dvh items-center justify-center px-6">
      <div className="w-full max-w-sm">
        <Link to="/" className="font-display text-lg font-semibold tracking-tight text-fg">
          Coach Bill
        </Link>

        <div className="mt-4 rounded-card border border-edge bg-surface p-6">
          {sentTo ? (
            <div className="flex flex-col gap-4">
              <h1 className="font-display text-xl font-semibold text-fg">Check your inbox</h1>
              <p className="text-sm leading-relaxed text-fg-muted">
                We sent a sign-in link to <span className="font-mono text-fg">{sentTo}</span>.
                Click it to verify your email — the tab you land in will take it from there.
              </p>
              <button
                type="button"
                onClick={() => void handleResend(sentTo)}
                className={secondaryButtonClasses}
              >
                {resent ? 'Sent again — check spam too' : 'Resend the link'}
              </button>
            </div>
          ) : (
            <form onSubmit={(e) => void handleSubmit(e)} className="flex flex-col gap-4">
              <h1 className="font-display text-xl font-semibold text-fg">
                {signup ? 'Start your free month' : 'Welcome back'}
              </h1>

              <label className="flex flex-col gap-1.5">
                <span className="font-mono text-xs tracking-wider text-fg-muted uppercase">
                  Email
                </span>
                <input
                  type="email"
                  required
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  className={`${inputClasses} font-mono`}
                />
              </label>

              <label className="flex flex-col gap-1.5">
                <span className="font-mono text-xs tracking-wider text-fg-muted uppercase">
                  Password
                </span>
                <input
                  type="password"
                  required
                  minLength={6}
                  autoComplete={signup ? 'new-password' : 'current-password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={signup ? 'At least 6 characters' : 'Your password'}
                  className={inputClasses}
                />
              </label>

              {!signup && (
                <p className="-mt-2 text-sm">
                  {resetSent ? (
                    <span className="text-fg-muted">Reset link sent — check your inbox.</span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => void handleForgotPassword()}
                      className="text-fg-muted transition-colors duration-150 hover:text-fg"
                    >
                      Forgot password?
                    </button>
                  )}
                </p>
              )}

              {error && <p className="text-sm text-fg-muted">{error}</p>}

              {needsConfirmation && (
                <div className="flex flex-col gap-2">
                  <p className="text-sm text-fg-muted">
                    That email isn&rsquo;t verified yet — click the link we sent you first.
                  </p>
                  <button
                    type="button"
                    onClick={() => void handleResend(email)}
                    className={secondaryButtonClasses}
                  >
                    {resent ? 'Sent — check spam too' : 'Resend the verification link'}
                  </button>
                </div>
              )}

              <button type="submit" disabled={busy} className={primaryButtonClasses}>
                {busy ? 'One moment…' : signup ? 'Create account' : 'Sign in'}
              </button>

              <div className="flex items-center gap-3" aria-hidden>
                <span className="h-px flex-1 bg-edge" />
                <span className="font-mono text-xs text-fg-muted">or</span>
                <span className="h-px flex-1 bg-edge" />
              </div>

              <button
                type="button"
                onClick={() => void handleGoogle()}
                disabled={busy}
                className={secondaryButtonClasses}
              >
                Continue with Google
              </button>
            </form>
          )}
        </div>

        {!sentTo && (
          <p className="mt-4 text-sm text-fg-muted">
            {signup ? (
              <>
                Already have an account?{' '}
                <button
                  type="button"
                  onClick={() => switchMode('signin')}
                  className="font-semibold text-fg hover:text-accent"
                >
                  Sign in
                </button>
              </>
            ) : (
              <>
                New here?{' '}
                <button
                  type="button"
                  onClick={() => switchMode('signup')}
                  className="font-semibold text-fg hover:text-accent"
                >
                  Start your free month
                </button>
              </>
            )}
          </p>
        )}
      </div>
    </main>
  )
}

export default Login
