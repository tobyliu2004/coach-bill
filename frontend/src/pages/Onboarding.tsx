import { useState } from 'react'
import type { FormEvent } from 'react'
import { useAuth } from '../auth/useAuth'
import { api } from '../lib/client'

const GOAL_PRESETS = ['Build muscle', 'Get stronger', 'Lose fat', 'Just be consistent'] as const

const inputClasses =
  'w-full rounded-control border border-edge-strong bg-bg px-3 py-2.5 text-sm text-fg ' +
  'placeholder:text-fg-muted focus:border-fg-muted focus:outline-none'

/**
 * First-login setup: what Bill needs before he can coach. Completing this stamps
 * consented_at server-side and sets goal — the signal that onboarding is done
 * (ProtectedRoute then routes /onboarding -> /app on its own).
 */
function Onboarding() {
  const auth = useAuth()
  const [displayName, setDisplayName] = useState('')
  const [goal, setGoal] = useState('')
  const [weightUnit, setWeightUnit] = useState<'lb' | 'kg'>('lb')
  const [consented, setConsented] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(false)
    setBusy(true)
    try {
      await api.updateMe({
        ...(displayName.trim() ? { display_name: displayName.trim() } : {}),
        goal: goal.trim(),
        weight_unit: weightUnit,
        // The browser knows the IANA zone; check-ins need it for "today".
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        // Only ever true here — the checkbox below gates the submit, so consented_at
        // is stamped strictly after the disclaimer was seen and acknowledged.
        consent: consented,
      })
      // Context refresh flips onboarded -> true; the route guard moves us to /app.
      await auth.refreshProfile()
    } catch {
      setError(true)
      setBusy(false)
    }
  }

  return (
    <main className="flex min-h-dvh items-center justify-center px-6">
      <div className="w-full max-w-sm">
        <span className="font-display text-lg font-semibold tracking-tight text-fg">
          Coach Bill
        </span>

        <form
          onSubmit={(e) => void handleSubmit(e)}
          className="mt-4 flex flex-col gap-5 rounded-card border border-edge bg-surface p-6"
        >
          <div className="flex flex-col gap-1">
            <h1 className="font-display text-xl font-semibold text-fg">Before your first rep</h1>
            <p className="text-sm leading-relaxed text-fg-muted">
              Thirty seconds — Bill coaches better when he knows what you&rsquo;re after.
            </p>
          </div>

          <label className="flex flex-col gap-1.5">
            <span className="font-mono text-xs tracking-wider text-fg-muted uppercase">
              What should Bill call you?
            </span>
            <input
              type="text"
              maxLength={80}
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Optional"
              className={inputClasses}
            />
          </label>

          <div className="flex flex-col gap-1.5">
            <span className="font-mono text-xs tracking-wider text-fg-muted uppercase">
              Your goal
            </span>
            <div className="flex flex-wrap gap-2">
              {GOAL_PRESETS.map((preset) => (
                <button
                  key={preset}
                  type="button"
                  onClick={() => setGoal(preset)}
                  className={`rounded-control border px-3 py-1.5 text-sm transition-colors duration-150 ${
                    goal === preset
                      ? 'border-accent text-fg'
                      : 'border-edge-strong text-fg-muted hover:border-fg-muted'
                  }`}
                >
                  {preset}
                </button>
              ))}
            </div>
            <textarea
              required
              minLength={3}
              maxLength={500}
              rows={2}
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder='Or say it your way — "cut to 175 without losing my bench"'
              className={`${inputClasses} resize-none`}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <span className="font-mono text-xs tracking-wider text-fg-muted uppercase">
              Weights in
            </span>
            <div className="flex gap-2 font-mono">
              {(['lb', 'kg'] as const).map((unit) => (
                <button
                  key={unit}
                  type="button"
                  onClick={() => setWeightUnit(unit)}
                  className={`flex-1 rounded-control border px-3 py-2 text-sm transition-colors duration-150 ${
                    weightUnit === unit
                      ? 'border-accent text-fg'
                      : 'border-edge-strong text-fg-muted hover:border-fg-muted'
                  }`}
                >
                  {unit}
                </button>
              ))}
            </div>
          </div>

          <label className="flex items-start gap-2.5 text-sm leading-relaxed text-fg-muted">
            <input
              type="checkbox"
              checked={consented}
              onChange={(e) => setConsented(e.target.checked)}
              className="mt-1 accent-accent"
            />
            <span>
              I understand Coach Bill is an AI training log and coach — not a doctor, and
              nothing it says is medical advice.
            </span>
          </label>

          {error && (
            <p role="alert" className="text-sm text-fg-muted">
              That didn&rsquo;t save — try again.
            </p>
          )}

          <button
            type="submit"
            disabled={busy || goal.trim().length < 3 || !consented}
            className="w-full rounded-control bg-accent px-5 py-2.5 text-sm font-semibold text-accent-ink transition-transform duration-150 ease-out-expo hover:scale-[1.02] active:scale-[0.98] disabled:opacity-50 disabled:hover:scale-100"
          >
            {busy ? 'Saving…' : 'Meet your coach'}
          </button>
        </form>
      </div>
    </main>
  )
}

export default Onboarding
