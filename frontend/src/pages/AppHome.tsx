import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '../auth/useAuth'
import { api } from '../lib/client'
import type { CheckIn } from '../lib/api'
import { errorAction, factsView, listView } from '../lib/checkInView'
import { formatMacros, formatSleep, formatWeight, toSetLines } from '../lib/formatFacts'

/**
 * The daily app shell + the text check-in flow. Deliberately quiet — no Lenis, no
 * signature moments; those are spent on marketing. This screen optimizes for speed:
 * type a check-in, it lands in today's list instantly, delete reconciles on the spot.
 * Logging is a repeated action, so nothing here animates (design.md).
 */

const composeClasses =
  'w-full resize-none rounded-control border border-edge-strong bg-bg px-3 py-3 text-sm ' +
  'text-fg placeholder:text-fg-muted focus:border-fg-muted focus:outline-none'

// created_at is data → mono, tabular. Local time, since "today" is already the user's day.
function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

/** One extracted fact: a quiet label and the number it stands for. */
function FactRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <span className="min-w-0 truncate font-mono text-xs text-fg-muted">{label}</span>
      <span className="shrink-0 font-mono text-xs tabular-nums text-fg">{value}</span>
    </div>
  )
}

/**
 * What Bill read out of a check-in — the trust mechanism for the whole AI loop. If the user
 * can't see what was extracted, they can't tell a good parse from a wrong one, and a wrong
 * number they never see is worse than no number at all.
 *
 * Rendering notes (design.md): the card already spent `bg-surface`, so this separates with a
 * `border-edge` divider INSIDE it rather than stepping up to `bg-raised` (spec'd for
 * popovers). Every number is `font-mono tabular-nums`. Nothing animates — the list is a
 * repeated-action surface, and animating height is banned outright.
 */
function Facts({ checkIn, unit }: { checkIn: CheckIn; unit: 'lb' | 'kg' }) {
  const view = factsView(checkIn)

  // 'failed' and 'none' must never look alike: a failure that renders as an empty state is
  // indistinguishable from data loss (#18's exact bug). Muted, not red — there is no red
  // token, and this is information, not an emergency.
  if (view.kind === 'failed') {
    return (
      <p role="alert" className="mt-3 border-t border-edge pt-3 font-mono text-xs text-fg-muted">
        Bill couldn’t read this one. Your words are saved.
      </p>
    )
  }
  // Nothing to extract is SUCCESS (Toby's row-11 call) — render the text and stop. No block,
  // no error, nothing that implies something went wrong.
  if (view.kind === 'none') return null

  const { facts, partial } = view
  return (
    <div className="mt-3 flex flex-col gap-1.5 border-t border-edge pt-3">
      {toSetLines(facts.sets, unit).map((line) => (
        <FactRow
          key={line.key}
          label={line.exercise}
          value={line.load === null ? line.volume : `${line.volume} · ${line.load}`}
        />
      ))}
      {facts.nutrition.map((entry) => (
        <FactRow key={entry.id} label={entry.description} value={formatMacros(entry)} />
      ))}
      {facts.sleep.map((entry) => (
        <FactRow key={entry.id} label="sleep" value={formatSleep(entry.hours, entry.quality)} />
      ))}
      {facts.bodyweight.map((entry) => (
        <FactRow
          key={entry.id}
          label="bodyweight"
          value={formatWeight(entry.weight_kg, unit) ?? '—'}
        />
      ))}
      {partial && (
        <p role="alert" className="pt-1 font-mono text-xs text-fg-muted">
          One item didn’t read — the rest is logged.
        </p>
      )}
    </div>
  )
}

function AppHome() {
  const { profile, session, signOut } = useAuth()
  // ProtectedRoute only renders this once the profile is loaded.
  const name = profile?.display_name ?? session?.user.email ?? 'you'
  // Facts are stored in canonical kg; show them back in the unit the user actually types in.
  // Same fallback as the column's own default.
  const unit = profile?.weight_unit ?? 'lb'

  const [text, setText] = useState('')
  const [checkIns, setCheckIns] = useState<CheckIn[]>([])
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // A rejected token means "signed out" — mirror AuthProvider and sign out rather than
  // stranding the user on a broken shell. Everything else is a transient in-app error.
  // The decision itself lives in `errorAction` (tested); this only carries it out.
  const onError = useCallback(
    (err: unknown, message: () => void): void => {
      if (errorAction(err).kind === 'sign-out') void signOut()
      else message()
    },
    [signOut],
  )

  const refresh = useCallback(async () => {
    try {
      setCheckIns(await api.listCheckIns())
      setLoadFailed(false)
    } catch (err) {
      onError(err, () => setLoadFailed(true))
    } finally {
      setLoading(false)
    }
  }, [onError])

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
    } catch (err) {
      onError(err, () => setError('That didn’t save — try again.'))
    } finally {
      setBusy(false)
    }
  }

  const view = listView({ loading, loadFailed, checkIns })

  async function handleDelete(id: string) {
    setError(null)
    try {
      await api.deleteCheckIn(id)
      // Drop it locally — no refetch, no animation; a delete should feel instant.
      setCheckIns((rows) => rows.filter((row) => row.id !== id))
    } catch (err) {
      onError(err, () => setError('Could not delete that — try again.'))
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

        {/* Which state this is comes from `listView` (tested); this only renders it. The
            load-failed and empty branches must stay distinct — "you have no check-ins" when
            the fetch actually failed reads as data loss. */}
        {view.kind === 'load-failed' && (
          <p role="alert" className="font-mono text-xs text-fg-muted">
            Couldn’t load today’s check-ins — refresh to try again.
          </p>
        )}

        {view.kind === 'list' && (
          <section className="flex flex-col gap-3">
            <div className="flex items-baseline justify-between">
              <span className="font-mono text-xs tracking-wider text-fg-muted uppercase">Today</span>
              <span className="font-mono text-xs tabular-nums text-fg-muted">
                {view.checkIns.length} logged
              </span>
            </div>
            <ul className="flex flex-col gap-2">
              {view.checkIns.map((checkIn) => (
                <li
                  key={checkIn.id}
                  className="rounded-card border border-edge bg-surface px-4 py-3"
                >
                  <div className="flex items-start justify-between gap-4">
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
                  </div>
                  <Facts checkIn={checkIn} unit={unit} />
                </li>
              ))}
            </ul>
          </section>
        )}

        {view.kind === 'empty' && (
          <div className="flex flex-col items-start gap-3">
            <h1 className="font-display text-display-sm text-fg">Nothing logged today.</h1>
            <p className="max-w-md text-base leading-relaxed text-fg-muted">
              Type your first set above — “squat 225 5×5, slept 7h” — and it lands here.
            </p>
          </div>
        )}
      </main>
    </div>
  )
}

export default AppHome
