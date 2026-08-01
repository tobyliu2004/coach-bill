import type { CheckIn } from '../lib/api'
import { factsView } from '../lib/checkInView'
import { formatMacros, formatSleep, formatWeight, toSetLines } from '../lib/formatFacts'

/**
 * What Bill read out of a check-in — the trust mechanism for the whole AI loop. If the user
 * can't see what was extracted, they can't tell a good parse from a wrong one, and a wrong
 * number they never see is worse than no number at all.
 *
 * Lifted out of AppHome when History needed the identical block. Deliberately a shared
 * component rather than a page-to-page import: History importing AppHome would pull AppHome's
 * whole module — compose box, submit and delete handlers — into History's lazy chunk, which
 * defeats the code split the routes exist to create.
 *
 * Rendering notes (design.md): the card already spent `bg-surface`, so this separates with a
 * `border-edge` divider INSIDE it rather than stepping up to `bg-raised` (spec'd for
 * popovers). Every number is `font-mono tabular-nums`. Nothing animates — the list is a
 * repeated-action surface, and animating height is banned outright.
 */

/** One extracted fact: a quiet label and the number it stands for. */
export function FactRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <span className="min-w-0 truncate font-mono text-xs text-fg-muted">{label}</span>
      <span className="shrink-0 font-mono text-xs tabular-nums text-fg">{value}</span>
    </div>
  )
}

export function Facts({ checkIn, unit }: { checkIn: CheckIn; unit: 'lb' | 'kg' }) {
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
  // Something was found and then dropped, and nothing else survived. Distinct from 'none':
  // there IS something to say. Silence here would be a lie of omission.
  if (view.kind === 'dropped') {
    return (
      <p role="alert" className="mt-3 border-t border-edge pt-3 font-mono text-xs text-fg-muted">
        Bill couldn’t read that exercise, so nothing was logged. Your words are saved.
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
        <FactRow key={entry.id} label="bodyweight" value={formatWeight(entry.weight_kg, unit) ?? '—'} />
      ))}
      {partial && (
        <p role="alert" className="pt-1 font-mono text-xs text-fg-muted">
          One item didn’t read — the rest is logged.
        </p>
      )}
    </div>
  )
}
