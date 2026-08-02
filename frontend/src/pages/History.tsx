import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '../auth/useAuth'
import { AppShell } from '../components/AppShell'
import { CoachReply } from '../components/CoachReply'
import { Facts } from '../components/Facts'
import type { CheckIn } from '../lib/api'
import { errorAction } from '../lib/checkInView'
import { api } from '../lib/client'
import { formatTime } from '../lib/formatFacts'
import { dayLabel, historyRequest, historyView } from '../lib/history'

/**
 * Everything you've logged, grouped by day. The first screen in the app that looks backwards.
 *
 * Every decision it renders — which state this is, which day a row belongs to, what that day
 * is called — comes from `lib/history.ts` and is tested there. This file only renders. That
 * split is the #18 retro's lesson: the bugs the reviewers caught on that ticket were all
 * "which state is this?", and none were catchable inside a `.tsx` this project's vitest
 * cannot mount.
 *
 * Quiet, like the rest of the daily app: no animation, no signature moment, no chart. Reading
 * your own log is not a moment, it's a reference.
 */

function History() {
  const { profile, signOut } = useAuth()
  // Facts are stored in canonical kg; show them back in the unit the user actually types in.
  const unit = profile?.weight_unit ?? 'lb'
  // The USER's zone, from their profile — not the browser's. A traveller's laptop set to
  // another zone must not relabel their evening check-in "Yesterday" (#18's bug on the
  // label). The SAME zone feeds the day heading and the clock time inside each card, so the
  // two can never disagree about which day a check-in belongs to.
  const timezone = profile?.timezone ?? null
  // THE #40 SEAM: which window, and whose "today", are now one tested decision instead of
  // two inline expressions nothing could assert. The instant is read once per render and
  // passed in, never inside the helper.
  const { days: historyDays, today } = historyRequest(profile ?? null, new Date())

  const [checkIns, setCheckIns] = useState<CheckIn[]>([])
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)

  // A rejected token means "signed out" — mirror AppHome rather than stranding the user on a
  // screen that can never succeed. The decision lives in `errorAction` (tested, shared with
  // the Today screen); this only carries it out.
  const load = useCallback(async () => {
    try {
      setCheckIns(await api.listCheckIns(historyDays))
      setLoadFailed(false)
    } catch (err) {
      if (errorAction(err).kind === 'sign-out') void signOut()
      else setLoadFailed(true)
    } finally {
      setLoading(false)
    }
  }, [historyDays, signOut])

  useEffect(() => {
    void load()
  }, [load])

  const view = historyView({ loading, loadFailed, checkIns })

  return (
    <AppShell>
      <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-8 px-6 py-12">
        <div className="flex items-baseline justify-between">
          <h1 className="font-mono text-xs tracking-wider text-fg-muted uppercase">History</h1>
          <span className="font-mono text-xs tabular-nums text-fg-muted">
            last {historyDays} days
          </span>
        </div>

        {/* load-failed and empty must stay distinct: "you have no check-ins" when the fetch
            actually failed reads as data loss. Both states come from `historyView`. */}
        {view.kind === 'load-failed' && (
          <p role="alert" className="font-mono text-xs text-fg-muted">
            Couldn’t load your history — refresh to try again.
          </p>
        )}

        {/* Scoped to the WINDOW, not the account. "Nothing here yet" would tell a returning
            user who took a month off that their history doesn't exist — the same
            failure-reads-as-data-loss shape rows 21/22 exist to prevent, one level down.
            We only know this window is empty; we know nothing about the year before it. */}
        {view.kind === 'empty' && (
          <div className="flex flex-col items-start gap-3">
            <p className="font-display text-display-sm text-fg">
              Nothing in the last {historyDays} days.
            </p>
            <p className="max-w-md text-base leading-relaxed text-fg-muted">
              Anything you log on Today shows up here, grouped by the day you logged it.
            </p>
          </div>
        )}

        {view.kind === 'days' && (
          <div className="flex flex-col gap-8">
            {view.days.map((day) => (
              <section key={day.date} className="flex flex-col gap-3">
                <div className="flex items-baseline justify-between">
                  {/* A date is data (design.md): tabular-nums so "Jul 5" and "Jul 25" line
                      up down the page instead of jittering against each other. */}
                  <h2 className="font-mono text-xs tracking-wider tabular-nums text-fg-muted uppercase">
                    {dayLabel(day.date, today)}
                  </h2>
                  <span className="font-mono text-xs tabular-nums text-fg-muted">
                    {day.checkIns.length} logged
                  </span>
                </div>
                <ul className="flex flex-col gap-2">
                  {day.checkIns.map((checkIn) => (
                    <li key={checkIn.id} className="rounded-card border border-edge bg-surface px-4 py-3">
                      <div className="flex min-w-0 flex-col gap-1">
                        <p className="font-mono text-sm leading-relaxed whitespace-pre-wrap break-words text-fg">
                          {checkIn.raw_text}
                        </p>
                        <span className="font-mono text-xs tabular-nums text-fg-muted">
                          {formatTime(checkIn.created_at, timezone)}
                        </span>
                      </div>
                      <Facts checkIn={checkIn} unit={unit} />
                      {/* Read-only here. History never REQUESTS a reply — it renders the
                          ones already stored — so `requesting`/`failed` are both false and
                          `replyView` can only return 'reply' or 'none'. `onRetry` is
                          therefore unreachable; it stays required on the component so the
                          Today screen cannot forget to pass one.

                          `live={false}` is the load-bearing prop: thirty cards mount at
                          once here, and announcing every one would be the barrage open as
                          issue #41. */}
                      <CoachReply
                        checkIn={checkIn}
                        requesting={false}
                        failed={false}
                        live={false}
                        onRetry={() => {}}
                      />
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
      </main>
    </AppShell>
  )
}

export default History
