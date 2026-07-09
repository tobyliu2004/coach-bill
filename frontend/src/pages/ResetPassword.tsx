import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { useAuth } from '../auth/useAuth'
import { supabase } from '../lib/supabase'

const inputClasses =
  'w-full rounded-control border border-edge-strong bg-bg px-3 py-2.5 text-sm text-fg ' +
  'placeholder:text-fg-muted focus:border-fg-muted focus:outline-none'

/**
 * Where the password-recovery email link lands. The link signs the user into a
 * short-lived recovery session (via /auth/... -> detectSessionInUrl), and this page
 * sets the new password. resolveDestination deliberately never redirects away from
 * /auth/reset so the recovery session can finish its job.
 */
function ResetPassword() {
  const auth = useAuth()
  const navigate = useNavigate()
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setBusy(true)
    const { error: updateError } = await supabase.auth.updateUser({ password })
    setBusy(false)
    if (updateError) {
      setError(updateError.message)
    } else {
      navigate('/app', { replace: true })
    }
  }

  if (auth.status === 'loading') return null

  if (auth.status === 'signedOut') {
    return (
      <main className="flex min-h-dvh flex-col items-center justify-center gap-4 px-6 text-center">
        <p className="max-w-sm text-sm leading-relaxed text-fg-muted">
          This reset link has expired or was already used. Request a fresh one from the
          sign-in page.
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
      <div className="w-full max-w-sm">
        <span className="font-display text-lg font-semibold tracking-tight text-fg">
          Coach Bill
        </span>
        <form
          onSubmit={(e) => void handleSubmit(e)}
          className="mt-4 flex flex-col gap-4 rounded-card border border-edge bg-surface p-6"
        >
          <h1 className="font-display text-xl font-semibold text-fg">Set a new password</h1>
          <label className="flex flex-col gap-1.5">
            <span className="font-mono text-xs tracking-wider text-fg-muted uppercase">
              New password
            </span>
            <input
              type="password"
              required
              minLength={6}
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="At least 6 characters"
              className={inputClasses}
            />
          </label>
          {error && (
            <p role="alert" className="text-sm text-fg-muted">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-control bg-accent px-5 py-2.5 text-sm font-semibold text-accent-ink transition-transform duration-150 ease-out-expo hover:scale-[1.02] active:scale-[0.98] disabled:opacity-50 disabled:hover:scale-100"
          >
            {busy ? 'Saving…' : 'Save and continue'}
          </button>
        </form>
      </div>
    </main>
  )
}

export default ResetPassword
