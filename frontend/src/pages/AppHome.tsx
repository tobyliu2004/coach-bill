import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '../auth/useAuth'
import { api } from '../lib/client'
import type { CheckIn } from '../lib/api'

/**
 * The daily app shell + the text check-in flow. Deliberately quiet — no Lenis, no
 * signature moments; those are spent on marketing. This screen optimizes for speed:
 * type a check-in, it lands in today's list instantly, delete reconciles on the spot.
 * Logging is a repeated action, so nothing here animates (design.md).
 */

const composeClasses =
  'w-full resize-none rounded-control border border-edge-strong bg-bg px-3.5 py-3 text-sm ' +
  'text-fg placeholder:text-fg-muted focus:border-fg-muted focus:outline-none'

// created_at is data → mono, tabular. Local time, since "today" is already the user's day.
function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

function AppHome() {
  const { profile, session, signOut } = useAuth()
  // ProtectedRoute only renders this once the profile is loaded.
  const name = profile?.display_name ?? session?.user.email ?? 'you'

  const [text, setText] = useState('')
  const [checkIns, setCheckIns] = useState<CheckIn[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setCheckIns(await api.listCheckIns())
      setError(null)
    } catch {
      setError('Could not load today’s check-ins.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  async function submit() {
    const body = text.trim()
    if (!body || busy) return
    setBusy(true)
    setError(null)
    try {
      await api.createCheckIn(body)
      setText('')
      await refresh()
    } catch {
      setError('That didn’t save — try again.')
    } finally {
      setBusy(false)
    }
  }

  async function handleDelete(id: string) {
    setError(null)
    try {
      await api.deleteCheckIn(id)
      // Drop it locally — no refetch, no animation; a delete should feel instant.
      setCheckIns((rows) => rows.filter((row) => row.id !== id))
    } catch {
      setError('Could not delete that — try again.')
    }
  }

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="border-b border-edge">
        <div className="mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-4">
          <span className="font-display text-lg font-semibold tracking-tight text-fg">
            Coach Bill
          </span>
          <div className="flex items-center gap-4">
            <span className="font-mono text-xs text-fg-muted">{name}</span>
            <button
              type="button"
              onClick={() => void signOut()}
              className="rounded-control border border-edge-strong px-3 py-1.5 text-xs font-semibold text-fg transition-colors duration-150 hover:border-fg-muted"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-8 px-6 py-12">
        {profile?.goal && (
          <p className="font-mono text-xs tracking-wider text-fg-muted uppercase">
            Goal — <span className="text-fg normal-case">{profile.goal}</span>
          </p>
        )}

        <form
          onSubmit={(e) => {
            e.preventDefault()
            void submit()
          }}
          className="flex flex-col gap-3"
        >
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                e.preventDefault()
                void submit()
              }
            }}
            maxLength={4000}
            rows={3}
            placeholder="bench 135 4×8, slept 6h, knee felt tweaky"
            className={composeClasses}
          />
          <div className="flex items-center justify-between">
            <span className="font-mono text-xs tabular-nums text-fg-muted">
              {text.trim().length > 0 ? `${text.trim().length}/4000` : 'Bill logs every set'}
            </span>
            <button
              type="submit"
              disabled={!text.trim() || busy}
              className="rounded-control bg-accent px-5 py-2 text-sm font-semibold text-accent-ink transition-transform duration-150 ease-out-expo hover:scale-[1.02] active:scale-[0.98] disabled:opacity-50 disabled:hover:scale-100"
            >
              {busy ? 'Logging…' : 'Log check-in'}
            </button>
          </div>
        </form>

        {error && (
          <p role="alert" className="font-mono text-xs text-fg-muted">
            {error}
          </p>
        )}

        {!loading &&
          (checkIns.length > 0 ? (
            <section className="flex flex-col gap-3">
              <div className="flex items-baseline justify-between">
                <span className="font-mono text-xs tracking-wider text-fg-muted uppercase">
                  Today
                </span>
                <span className="font-mono text-xs tabular-nums text-fg-muted">
                  {checkIns.length} logged
                </span>
              </div>
              <ul className="flex flex-col gap-2">
                {checkIns.map((checkIn) => (
                  <li
                    key={checkIn.id}
                    className="flex items-start justify-between gap-4 rounded-card border border-edge bg-surface px-4 py-3"
                  >
                    <div className="flex min-w-0 flex-col gap-1">
                      <p className="font-mono text-sm leading-relaxed whitespace-pre-wrap break-words text-fg">
                        {checkIn.raw_text}
                      </p>
                      <span className="font-mono text-xs tabular-nums text-fg-muted">
                        {formatTime(checkIn.created_at)}
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={() => void handleDelete(checkIn.id)}
                      aria-label="Delete check-in"
                      className="shrink-0 rounded-control px-2 py-1 font-mono text-xs text-fg-muted transition-colors duration-150 hover:text-fg focus:text-fg focus:outline-none"
                    >
                      Delete
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ) : (
            <div className="flex flex-col items-start gap-3">
              <h1 className="font-display text-display-sm text-fg">Nothing logged today.</h1>
              <p className="max-w-md text-base leading-relaxed text-fg-muted">
                Type your first set above — “squat 225 5×5, slept 7h” — and it lands here.
              </p>
            </div>
          ))}
      </main>
    </div>
  )
}

export default AppHome
