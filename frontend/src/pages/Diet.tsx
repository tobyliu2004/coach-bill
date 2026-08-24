import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '../auth/useAuth'
import { AppShell } from '../components/AppShell'
import { ApiError, type Trends } from '../lib/api'
import { errorAction } from '../lib/checkInView'
import { api } from '../lib/client'
import { type DietMeasures, dietRequest, dietView, todaysIntake } from '../lib/plan'

/**
 * Today's food against today's target.
 *
 * NO ENDPOINT OF ITS OWN, deliberately (#51, amendment 3): this composes
 * `GET /trends?days=1` for what they ate with `GET /plans/current` for what they were
 * aiming at. The summing happens in Postgres, where it is graded against a real database —
 * three meals across three check-ins arrive as ONE already-summed point (row 20). Nothing
 * on this screen adds anything up.
 *
 * RENDER ONLY. `lib/plan.ts` decides which point is today's, whether there is a target at
 * all, and what "nothing logged" means; all of it is tested there because node-env vitest
 * cannot mount a `.tsx`.
 */

const MACROS: { key: keyof DietMeasures; label: string; unit: string }[] = [
  { key: 'calories', label: 'Calories', unit: 'kcal' },
  { key: 'protein_g', label: 'Protein', unit: 'g' },
  { key: 'carbs_g', label: 'Carbs', unit: 'g' },
  { key: 'fat_g', label: 'Fat', unit: 'g' },
]

function Diet() {
  const { profile, signOut } = useAuth()
  // THE #40 SEAM: which window, and whose "today". One tested decision, passed the instant.
  const { days, today } = dietRequest(profile ?? null, new Date())

  const [trends, setTrends] = useState<Trends | null>(null)
  const [plan, setPlan] = useState<Parameters<typeof dietView>[0]['plan']>(null)
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)

  const load = useCallback(async () => {
    try {
      // Both at once. The two are independent — having no plan must not stop the food
      // showing, which is the whole of row 19.
      const [trendsResult, planResult] = await Promise.all([
        api.getTrends(days),
        // A 404 here is "no active plan", a NORMAL state, so it is folded into `null` rather
        // than failing the screen. Any other failure still rejects and is handled below.
        api.getCurrentPlan().catch((err: unknown) => {
          if (err instanceof ApiError && err.status === 404) return null
          throw err
        }),
      ])
      setTrends(trendsResult)
      setPlan(planResult)
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

  // `todaysIntake` returns null when the sparse series has no point for today — which is
  // "they logged nothing", not "we failed". `dietView` turns that into a rendered 0 against
  // the target, and that two-layer split is rows 20/21 (see lib/plan.ts).
  const view =
    trends === null ? null : dietView({ plan, consumed: todaysIntake(trends.nutrition, today) })

  return (
    <AppShell>
      <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-8 px-6 py-12">
        <div className="flex items-baseline justify-between gap-4">
          <h1 className="font-mono text-xs tracking-wider text-fg-muted uppercase">Diet</h1>
          <span className="font-mono text-xs tabular-nums text-fg-muted">today</span>
        </div>

        {loading && <p className="font-mono text-xs text-fg-muted">Loading today’s food…</p>}

        {!loading && loadFailed && (
          <div className="flex flex-col items-start gap-3">
            <p role="alert" className="font-mono text-xs text-fg-muted">
              Couldn’t load today’s food.
            </p>
            <button
              type="button"
              onClick={() => void load()}
              className="rounded-control border border-edge-strong px-3 py-1.5 text-xs font-semibold text-fg transition-colors duration-150 hover:border-fg-muted"
            >
              Try again
            </button>
          </div>
        )}

        {!loading && !loadFailed && view !== null && (
          <>
            <ul className="flex flex-col">
              {MACROS.map((macro) => (
                <li
                  key={macro.key}
                  className="flex items-baseline justify-between gap-4 border-b border-edge py-3 last:border-b-0"
                >
                  <span className="text-sm text-fg">{macro.label}</span>
                  {/* TWO NUMBERS, NEVER A PERCENTAGE (row 18). 1800 / 2400 is legible at a
                      glance and never rounds itself away; "75%" throws away both numbers
                      and reads as 0% on the first meal of the day. */}
                  <span className="font-mono text-sm tabular-nums slashed-zero text-fg">
                    {format(view.consumed[macro.key])}
                    {/* NO TARGET LINE AT ALL when there is no plan (row 19) — as opposed to
                        "0 / 2400" when there IS one and nothing is logged (row 21). Those
                        are different claims about the world and the screen must not collapse
                        them; a target of 0 would be the "peak 0 lb" bug all over again. */}
                    {view.kind === 'target' && (
                      <span className="text-fg-muted">
                        {' / '}
                        {format(view.target[macro.key])}
                      </span>
                    )}{' '}
                    <span className="text-fg-muted">{macro.unit}</span>
                  </span>
                </li>
              ))}
            </ul>

            {view.kind === 'no-target' && (
              <p className="max-w-md text-base leading-relaxed text-fg-muted">
                No targets yet — write a plan and Bill sets a daily calorie and macro goal
                you’ll see beside these numbers.
              </p>
            )}
          </>
        )}
      </main>
    </AppShell>
  )
}

/**
 * A parsed measure, or an em dash.
 *
 * `null` means "we have no number", which is NOT zero and must not render as one — the
 * distinction this whole screen is built around. `parseNumeric` produces null for a
 * malformed value, so a broken target reads as "no target for this macro" rather than as a
 * 0 kcal target somebody might act on.
 */
function format(value: number | null): string {
  return value === null ? '—' : String(value)
}

export default Diet
