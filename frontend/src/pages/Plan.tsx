import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '../auth/useAuth'
import { AppShell } from '../components/AppShell'
import { ApiError, type Plan as PlanPayload, type PlanDay } from '../lib/api'
import { errorAction } from '../lib/checkInView'
import { api } from '../lib/client'
import { monthDay } from '../lib/dates'
import { formatWeight } from '../lib/formatFacts'
import { planRequest, planView } from '../lib/plan'

/**
 * The screen this whole issue exists for. Bill could already WRITE a program in prose —
 * verified in prod — but his reply opened "The app can't save a program to your dashboard,
 * so write this down somewhere you'll see it." This is that sentence turned into a screen.
 *
 * RENDER ONLY. Which state this is, and which day is today, come from `lib/plan.ts` and are
 * tested there; vitest here is node-env and cannot mount a `.tsx`, so a decision left in
 * this file is a decision nothing can assert (#18/#39/#40, three times paid for).
 *
 * Quiet, like the rest of the daily app — no signature moment, no entrance animation. Amber
 * is spent on exactly two things: the generate button (the one primary action) and today's
 * date marker. Everything else is grayscale, so the day you are on is the first thing the
 * eye lands on.
 */

function Plan() {
  const { profile, signOut } = useAuth()
  const unit = profile?.weight_unit ?? 'lb'
  // THE #40 SEAM: whose "today" is one tested decision, not an inline expression. The
  // instant is read once per render and passed in, never reached for inside the helper.
  const { today } = planRequest(profile ?? null, new Date())

  const [plan, setPlan] = useState<PlanPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)
  const [generating, setGenerating] = useState(false)

  const load = useCallback(async () => {
    try {
      setPlan(await api.getCurrentPlan())
      setLoadFailed(false)
    } catch (err) {
      // ⚠️ A 404 IS NOT A FAILURE — it is "you have no plan yet", which is the normal state
      // for everyone who has never pressed the button. Treating it as an error would put a
      // red message in front of every new user; treating a real failure as a 404 would tell
      // someone their plan is gone. Rows 22/23 are exactly this pair staying apart.
      if (err instanceof ApiError && err.status === 404) {
        setPlan(null)
        setLoadFailed(false)
      } else if (errorAction(err).kind === 'sign-out') {
        void signOut()
      } else {
        setLoadFailed(true)
      }
    } finally {
      setLoading(false)
    }
  }, [signOut])

  useEffect(() => {
    void load()
  }, [load])

  const view = planView({ loading, loadFailed, generating, plan })

  async function generate() {
    // The view withholds the button while generating (row 24), so this cannot be re-entered
    // from the UI. The guard stays anyway: a keyboard repeat or a stale render is cheap to
    // rule out, and the thing being protected is a Sonnet call, not a round trip.
    if (!view.canGenerate) return
    setGenerating(true)
    setLoadFailed(false)
    try {
      setPlan(await api.createPlan(4))
    } catch (err) {
      if (errorAction(err).kind === 'sign-out') void signOut()
      else setLoadFailed(true)
    } finally {
      setGenerating(false)
    }
  }

  return (
    <AppShell>
      <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-8 px-6 py-12">
        <div className="flex items-baseline justify-between gap-4">
          <h1 className="font-mono text-xs tracking-wider text-fg-muted uppercase">Plan</h1>
          {view.kind === 'plan' && (
            <span className="font-mono text-xs tabular-nums text-fg-muted">
              {monthDay(view.plan.starts_on)} – {monthDay(view.plan.ends_on)}
            </span>
          )}
        </div>

        {/* FIVE STATES, FIVE SENTENCES. #43's bug is a fetch in flight rendering as
            nothing, which reads as "you have no plan" and invites the user to spend a
            model call generating one they already have. */}
        {view.kind === 'loading' && (
          <p className="font-mono text-xs text-fg-muted">Loading your plan…</p>
        )}

        {view.kind === 'generating' && (
          <p className="font-mono text-xs text-fg-muted">
            Bill is writing your plan… this takes a few seconds.
          </p>
        )}

        {/* An error the user can act on — never a silent "you have no plan" (row 23). */}
        {view.kind === 'load-failed' && (
          <div className="flex flex-col items-start gap-3">
            <p role="alert" className="font-mono text-xs text-fg-muted">
              Couldn’t load your plan.
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

        {view.kind === 'empty' && (
          <div className="flex flex-col items-start gap-3">
            <p className="font-display text-display-sm text-fg">You don’t have a plan yet.</p>
            <p className="max-w-md text-base leading-relaxed text-fg-muted">
              Bill writes four weeks off what you’ve actually logged — the split, the working
              weights, and a daily calorie and macro target you’ll see on Diet.
            </p>
          </div>
        )}

        {view.kind === 'plan' && <Program plan={view.plan} today={today} unit={unit} />}

        {/* One button, every state — which is what row 24's "cannot be double-fired" is
            actually about. `canGenerate` is answered by the view for all five states. */}
        <div className="flex flex-col items-start gap-2">
          <button
            type="button"
            onClick={() => void generate()}
            disabled={!view.canGenerate}
            className="w-fit rounded-control bg-accent px-5 py-2.5 text-sm font-semibold text-accent-ink transition-transform duration-150 ease-out-expo hover:scale-[1.02] active:scale-[0.98] disabled:opacity-50 disabled:hover:scale-100"
          >
            {view.kind === 'generating'
              ? 'Writing…'
              : view.kind === 'plan'
                ? 'Write a new plan'
                : 'Write my plan'}
          </button>
          {view.kind === 'plan' && (
            <p className="font-mono text-xs text-fg-muted">
              This replaces the plan above. The old one is archived, not deleted.
            </p>
          )}
        </div>
      </main>
    </AppShell>
  )
}

function Program({
  plan,
  today,
  unit,
}: {
  plan: PlanPayload
  today: string
  unit: 'lb' | 'kg'
}) {
  // Grouped by week for reading, not for arithmetic — `week_number` was computed server-side
  // by the pure `materialize`, so nothing here re-derives which week a date belongs to.
  const weeks = new Map<number, PlanDay[]>()
  for (const day of plan.days) {
    const bucket = weeks.get(day.week_number)
    if (bucket) bucket.push(day)
    else weeks.set(day.week_number, [day])
  }

  return (
    <div className="flex flex-col gap-8">
      <p className="max-w-md text-base leading-relaxed text-fg-muted">
        {plan.progression_note}
      </p>

      {[...weeks.entries()].map(([week, days]) => (
        <section key={week} className="flex flex-col gap-2">
          <h2 className="font-mono text-xs tracking-wider text-fg-muted uppercase">
            Week {week}
          </h2>
          <ul className="flex flex-col">
            {days.map((day) => (
              <Day key={day.id} day={day} isToday={day.day_date === today} unit={unit} />
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}

function Day({ day, isToday, unit }: { day: PlanDay; isToday: boolean; unit: 'lb' | 'kg' }) {
  return (
    <li className="flex gap-4 border-b border-edge py-3 last:border-b-0">
      <span
        className={`w-16 shrink-0 font-mono text-xs tabular-nums ${
          isToday ? 'text-accent' : 'text-fg-muted'
        }`}
      >
        {monthDay(day.day_date)}
      </span>
      <div className="flex flex-1 flex-col gap-1">
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-sm text-fg">{day.focus}</span>
          {/* Whether they actually trained. Computed server-side from their OWN workouts. */}
          {day.logged && (
            <span className="font-mono text-xs text-fg-muted">logged</span>
          )}
        </div>
        {day.items.length > 0 && (
          <ul className="flex flex-col gap-0.5">
            {day.items.map((item) => (
              <li key={item.id} className="font-mono text-xs tabular-nums text-fg-muted">
                {item.exercise_name} · {item.reps} reps
                {/* null, not 0 — a bodyweight movement has no load to print, and "0 lb"
                    would be this feature's own null-vs-zero lie (the "peak 0 lb" bug). */}
                {item.weight_kg !== null && ` · ${formatWeight(item.weight_kg, unit)}`}
              </li>
            ))}
          </ul>
        )}
      </div>
    </li>
  )
}

export default Plan
