import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '../auth/useAuth'
import { AppShell } from '../components/AppShell'
import { CoachReply } from '../components/CoachReply'
import { Facts } from '../components/Facts'
import { Skeleton } from '../components/Skeleton'
import { api } from '../lib/client'
import type { CheckIn } from '../lib/api'
import { errorAction, listView, todayRequest } from '../lib/checkInView'
import { formatTime } from '../lib/formatFacts'

/**
 * The text check-in flow — today, and only today. Deliberately quiet: no Lenis, no
 * signature moments; those are spent on marketing. This screen optimizes for speed:
 * type a check-in, it lands in today's list instantly, delete reconciles on the spot.
 * Logging is a repeated action, so nothing here animates (design.md).
 *
 * The header moved to `AppShell` and the extracted-facts block to `components/Facts` when
 * History needed both. Neither was copied — a duplicated header is how a sign-out button
 * ends up in two places and drifts apart.
 */

const composeClasses =
  'w-full resize-none rounded-control border border-edge-strong bg-bg px-3 py-3 text-sm ' +
  'text-fg placeholder:text-fg-muted focus:border-fg-muted focus:outline-none'

function AppHome() {
  const { profile, signOut } = useAuth()
  // Facts are stored in canonical kg; show them back in the unit the user actually types in.
  // Same fallback as the column's own default.
  const unit = profile?.weight_unit ?? 'lb'
  // The user's zone, not the browser's — the same source `entry_date` was stamped from, so
  // a logged time can never belong to a different day than the one it's filed under.
  const timezone = profile?.timezone ?? null
  // THE #40 SEAM. This screen's window is the one that must stay exactly ONE day: widening
  // it would be invisible on screen (today's rows still render first) while quietly
  // multiplying this endpoint's payload for every user on every load. Now pinned by a test.
  const { days: todayDays } = todayRequest(profile ?? null, new Date())

  const [text, setText] = useState('')
  const [checkIns, setCheckIns] = useState<CheckIn[]>([])
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Reply state is PER CHECK-IN, not per screen: today can hold several check-ins, and one
  // of them failing must not put every card into the error state. Sets of ids rather than a
  // flag, so the states stay independent.
  const [replyPending, setReplyPending] = useState<ReadonlySet<string>>(new Set())
  const [replyFailed, setReplyFailed] = useState<ReadonlySet<string>>(new Set())

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
      setCheckIns(await api.listCheckIns(todayDays))
      setLoadFailed(false)
    } catch (err) {
      onError(err, () => setLoadFailed(true))
    } finally {
      setLoading(false)
    }
  }, [onError, todayDays])

  useEffect(() => {
    void refresh()
  }, [refresh])


  /**
   * Ask Bill to reply to one check-in, and fold the answer into that row.
   *
   * Separate from `submit` because it is separately retryable — the server stores nothing
   * on a failure, so pressing "Try again" is a real action rather than a hope. The endpoint
   * is get-or-create, so calling it twice is safe and cheap: the second call returns the
   * stored reply without spending either model.
   */
  const requestReply = useCallback(
    async (id: string) => {
      setReplyPending((ids) => new Set(ids).add(id))
      setReplyFailed((ids) => {
        const next = new Set(ids)
        next.delete(id)
        return next
      })
      try {
        const reply = await api.requestReply(id)
        // Patch the one row rather than refetching the list: the reply is the only thing
        // that changed, and a refetch would rebuild every card mid-read.
        setCheckIns((rows) => rows.map((row) => (row.id === id ? { ...row, reply } : row)))
      } catch (err) {
        onError(err, () => setReplyFailed((ids) => new Set(ids).add(id)))
      } finally {
        setReplyPending((ids) => {
          const next = new Set(ids)
          next.delete(id)
          return next
        })
      }
    },
    [onError],
  )

  async function submit() {
    const body = text.trim()
    if (!body || busy) return
    setBusy(true)
    setError(null)
    try {
      // Use what POST returned rather than refetching the whole list. The response is read
      // back from the database through the same path GET uses, so it's the same row GET
      // would hand us — one round trip instead of two, and the new check-in lands instantly.
      const created = await api.createCheckIn(body)
      setText('')
      setCheckIns((rows) => [created, ...rows]) // newest first, matching the list's order
      // Ask for the reply, but do NOT await it inside submit: the check-in is already
      // saved and on screen, and Bill takes a few seconds. Blocking here would leave the
      // compose box disabled the whole time and make a slow coach look like a slow save.
      void requestReply(created.id)
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
    <AppShell>
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

        {/* Which state this is comes from `listView` (tested); this only renders it. All four
            branches must stay distinct — "you have no check-ins" when the fetch actually
            failed reads as data loss, and so does it when the fetch is merely still running
            (#43). `data-state` is the seam that lets a test assert WHICH state rendered as a
            category, instead of matching copy any screen might print. */}
        {view.kind === 'loading' && (
          <div data-state="loading">
            <Skeleton label="Loading today’s check-ins" count={2} />
          </div>
        )}

        {view.kind === 'load-failed' && (
          // The alert lives INSIDE the state slot rather than on it: the state seam says which
          // branch rendered, the role says what kind of thing it is, and they are separate
          // questions. Collapsing them onto one node also hides the alert from a scoped
          // `within(state)` query, which is how a test meant to prove the error is announced
          // would instead prove nothing.
          <div data-state="load-failed">
            <p role="alert" className="font-mono text-xs text-fg-muted">
              Couldn’t load today’s check-ins — refresh to try again.
            </p>
          </div>
        )}

        {view.kind === 'list' && (
          <section data-state="content" className="flex flex-col gap-3">
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
                        {formatTime(checkIn.created_at, timezone)}
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
                  {/* `live` — this is the screen the user just submitted on, so a reply
                      arriving is worth interrupting a screen reader for. History passes
                      false; same component, two contexts (#41). */}
                  <CoachReply
                    checkIn={checkIn}
                    requesting={replyPending.has(checkIn.id)}
                    failed={replyFailed.has(checkIn.id)}
                    live
                    onRequest={() => void requestReply(checkIn.id)}
                  />
                </li>
              ))}
            </ul>
          </section>
        )}

        {view.kind === 'empty' && (
          <div data-state="empty" className="flex flex-col items-start gap-3">
            <h1 className="font-display text-display-sm text-fg">Nothing logged today.</h1>
            <p className="max-w-md text-base leading-relaxed text-fg-muted">
              Type your first set above — “squat 225 5×5, slept 7h” — and it lands here.
            </p>
          </div>
        )}
      </main>
    </AppShell>
  )
}

export default AppHome
