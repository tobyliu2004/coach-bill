import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '../auth/useAuth'
import { AppShell } from '../components/AppShell'
import { BarChart } from '../components/BarChart'
import { Sparkline } from '../components/Sparkline'
import type { Trends as TrendsPayload } from '../lib/api'
import { errorAction } from '../lib/checkInView'
import { api } from '../lib/client'
import { monthDay } from '../lib/dates'
import { formatWeight } from '../lib/formatFacts'
import {
  barLayout,
  parseNumeric,
  type SeriesPoint,
  sparkline,
  trendsRequest,
  trendsView,
} from '../lib/trends'

/**
 * Am I actually progressing? /history answers "what did I log"; this answers the other one.
 *
 * Every decision and every coordinate on this screen comes from `lib/trends.ts` and is
 * tested there — which state this is, where each bar sits, whether a string is a number.
 * This file only renders. That split is the #18/#39 lesson: every bug those tickets shipped
 * was a decision, and none were catchable inside a `.tsx` this project's vitest cannot mount.
 *
 * Quiet, like the rest of the daily app. `History.tsx` says "no animation, no signature
 * moment" and a chart does not change that — this is a reference you check, not a moment.
 * Amber is spent entirely on the volume bars (design.md caps the accent at ~5%); the table,
 * the sparklines and the nav all stay grayscale so the one thing worth looking at first is
 * obvious.
 */

/** A measure column from the payload, parsed once, with the unmeasurable days dropped. */
function toPoints(rows: { date: string; value: string | null }[]): SeriesPoint[] {
  return rows.flatMap((row) => {
    const value = parseNumeric(row.value)
    // null is not 0 — a day with no tonnage to measure has no bar, rather than a bar of
    // height zero claiming the user lifted nothing (backend AC row 11).
    return value === null ? [] : [{ date: row.date, value }]
  })
}

function Trends() {
  const { profile, signOut } = useAuth()
  // Facts are stored in canonical kg; show them back in the unit the user actually types in.
  const unit = profile?.weight_unit ?? 'lb'
  // THE #40 SEAM. Which window, computed from whose "today", is one tested decision rather
  // than two inline expressions nothing could assert. The instant is read once per render
  // and passed in, never inside the helper.
  const request = trendsRequest(profile ?? null, new Date())
  const days = request.days

  const [trends, setTrends] = useState<TrendsPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)

  // A rejected token means "signed out" — mirror AppHome and History rather than stranding
  // the user on a screen that can never succeed. The decision lives in `errorAction`
  // (tested, shared with both other screens); this only carries it out.
  const load = useCallback(async () => {
    try {
      setTrends(await api.getTrends(days))
      setLoadFailed(false)
    } catch (err) {
      if (errorAction(err).kind === 'sign-out') void signOut()
      else setLoadFailed(true)
    } finally {
      setLoading(false)
    }
  }, [days, signOut])

  useEffect(() => {
    void load()
  }, [load])

  const view = trendsView({ loading, loadFailed, trends })

  return (
    <AppShell>
      <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-8 px-6 py-12">
        <div className="flex items-baseline justify-between">
          <h1 className="font-mono text-xs tracking-wider text-fg-muted uppercase">Trends</h1>
          <span className="font-mono text-xs tabular-nums text-fg-muted">last {days} days</span>
        </div>

        {/* load-failed and empty must stay distinct: "you have no data" when the fetch
            actually failed reads as data loss. Both states come from `trendsView`. */}
        {view.kind === 'load-failed' && (
          <p role="alert" className="font-mono text-xs text-fg-muted">
            Couldn’t load your trends — refresh to try again.
          </p>
        )}

        {/* Scoped to the WINDOW, not the account — the same reason History's is. We only
            know this window is empty; we know nothing about the year before it. */}
        {view.kind === 'empty' && (
          <div className="flex flex-col items-start gap-3">
            <p className="font-display text-display-sm text-fg">
              Nothing to chart in the last {days} days.
            </p>
            <p className="max-w-md text-base leading-relaxed text-fg-muted">
              Log a few sets on Today and this fills in — daily volume, your movements, sleep,
              bodyweight and calories.
            </p>
          </div>
        )}

        {view.kind === 'trends' && <Dashboard trends={view.trends} unit={unit} />}
      </main>
    </AppShell>
  )
}

function Dashboard({ trends, unit }: { trends: TrendsPayload; unit: 'lb' | 'kg' }) {
  // The axis is laid out from the SERVER's window, never from anything computed here — the
  // client never recomputes "today" (decision 2), which is the drift #40 is about.
  const { start_date: start, end_date: end } = trends

  const volume = barLayout(
    toPoints(trends.volume.map((p) => ({ date: p.date, value: p.volume_kg }))),
    start,
    end,
  )
  // Days the user trained but that carry no measurable tonnage. Positioned by the same
  // tested layout so a pushups-only day lands on its own date; the value is a placeholder
  // because only the x is used (the tick has a fixed height at the baseline).
  const bodyweightOnly = barLayout(
    trends.volume
      .filter((p) => p.volume_kg === null && p.bodyweight_sets > 0)
      .map((p) => ({ date: p.date, value: 1 })),
    start,
    end,
  )

  const sleep = sparkline(toPoints(trends.sleep.map((p) => ({ date: p.date, value: p.hours }))))
  const bodyweight = sparkline(
    toPoints(trends.bodyweight.map((p) => ({ date: p.date, value: p.weight_kg }))),
  )
  const calories = sparkline(
    toPoints(trends.nutrition.map((p) => ({ date: p.date, value: p.calories }))),
  )

  return (
    <div className="flex flex-col gap-10">
      <section className="flex flex-col gap-3">
        <div className="flex items-baseline justify-between">
          <h2 className="font-mono text-xs tracking-wider text-fg-muted uppercase">
            Daily volume
          </h2>
          <span className="font-mono text-xs tabular-nums text-fg">
            peak {formatWeight(String(volume.max), unit) ?? '—'}
          </span>
        </div>

        <BarChart
          bars={volume.bars}
          marks={bodyweightOnly.bars}
          label={`Daily training volume from ${monthDay(start)} to ${monthDay(end)}, peaking at ${
            formatWeight(String(volume.max), unit) ?? 'no measured load'
          }`}
        />

        {/* A date is data (design.md): tabular-nums so the axis doesn't jitter. Both ends
            come from the payload, so they can never disagree with the bars above them. */}
        <div className="flex items-baseline justify-between font-mono text-xs tabular-nums text-fg-muted">
          <span>{monthDay(start)}</span>
          <span>{monthDay(end)}</span>
        </div>

        {bodyweightOnly.bars.length > 0 && (
          <p className="font-mono text-xs text-fg-muted">
            {bodyweightOnly.bars.length} day{bodyweightOnly.bars.length === 1 ? '' : 's'} of
            bodyweight-only work — marked on the baseline, no load to measure.
          </p>
        )}
      </section>

      {trends.exercises.length > 0 && (
        <section className="flex flex-col gap-3">
          <h2 className="font-mono text-xs tracking-wider text-fg-muted uppercase">Movements</h2>
          <div className="overflow-x-auto rounded-card border border-edge bg-surface">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-edge">
                  <th className="px-4 py-2 font-mono text-xs font-normal tracking-wider text-fg-muted uppercase">
                    Movement
                  </th>
                  <th className="px-4 py-2 text-right font-mono text-xs font-normal tracking-wider text-fg-muted uppercase">
                    Sets
                  </th>
                  <th className="px-4 py-2 text-right font-mono text-xs font-normal tracking-wider text-fg-muted uppercase">
                    Reps
                  </th>
                  <th className="px-4 py-2 text-right font-mono text-xs font-normal tracking-wider text-fg-muted uppercase">
                    Volume
                  </th>
                  <th className="px-4 py-2 text-right font-mono text-xs font-normal tracking-wider text-fg-muted uppercase">
                    Heaviest
                  </th>
                </tr>
              </thead>
              <tbody>
                {trends.exercises.map((exercise) => (
                  <tr key={exercise.name} className="border-b border-edge last:border-b-0">
                    <td className="px-4 py-2 font-mono text-sm text-fg">{exercise.name}</td>
                    <td className="px-4 py-2 text-right font-mono text-sm tabular-nums text-fg-muted">
                      {exercise.sets}
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-sm tabular-nums text-fg-muted">
                      {exercise.reps}
                    </td>
                    {/* An em dash, not "0": a bodyweight movement has no tonnage to report,
                        and 0 would be a measurement we never took. */}
                    <td className="px-4 py-2 text-right font-mono text-sm tabular-nums text-fg">
                      {formatWeight(exercise.volume_kg, unit) ?? '—'}
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-sm tabular-nums text-fg-muted">
                      {formatWeight(exercise.heaviest_kg, unit) ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="flex flex-col gap-6">
        <h2 className="font-mono text-xs tracking-wider text-fg-muted uppercase">
          Sleep · bodyweight · calories
        </h2>
        <Trace
          title="Sleep"
          line={sleep}
          reading={sleep.points === '' ? null : `${trends.sleep[trends.sleep.length - 1].hours} h`}
          range={sleep.points === '' ? null : `${sleep.min}–${sleep.max} h`}
        />
        <Trace
          title="Bodyweight"
          line={bodyweight}
          reading={
            bodyweight.points === ''
              ? null
              : formatWeight(trends.bodyweight[trends.bodyweight.length - 1].weight_kg, unit)
          }
          range={
            bodyweight.points === ''
              ? null
              : `${formatWeight(String(bodyweight.min), unit)}–${formatWeight(
                  String(bodyweight.max),
                  unit,
                )}`
          }
        />
        <Trace
          title="Calories"
          line={calories}
          reading={
            calories.points === ''
              ? null
              : `${trends.nutrition[trends.nutrition.length - 1].calories} kcal`
          }
          range={calories.points === '' ? null : `${calories.min}–${calories.max} kcal`}
        />
      </section>
    </div>
  )
}

/**
 * One labelled sparkline row. `reading` is the most recent value, `range` the window's span.
 *
 * A sparkline is read as shape, so the numbers are printed beside it rather than inferred
 * from the line — and an empty series says so in words instead of drawing a flat line at
 * zero, which would claim a measurement that was never taken.
 */
function Trace({
  title,
  line,
  reading,
  range,
}: {
  title: string
  line: { points: string }
  reading: string | null
  range: string | null
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between">
        <span className="font-mono text-xs tracking-wider text-fg-muted uppercase">{title}</span>
        <span className="font-mono text-xs tabular-nums text-fg">
          {reading ?? <span className="text-fg-muted">nothing logged</span>}
        </span>
      </div>
      {line.points !== '' && <Sparkline points={line.points} label={`${title}: ${range}`} />}
      {range !== null && (
        <span className="font-mono text-xs tabular-nums text-fg-muted">{range}</span>
      )}
    </div>
  )
}

export default Trends
