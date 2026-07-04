import { useState } from 'react'
import type { Variants } from 'motion/react'
import * as m from 'motion/react-m'

/** The signature moment: a spoken check-in transforming into structured data. */

const cascade: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.12, delayChildren: 0.5 } },
}

const row: Variants = {
  hidden: { opacity: 0, y: 10 },
  show: { opacity: 1, y: 0 },
}

const EXTRACTED = [
  { label: 'bench press', value: '3×8 @ 135 lb' },
  { label: 'sleep', value: '6.0 h' },
  { label: 'note', value: '“knee felt tweaky” → remembered' },
] as const

export function CheckInDemo() {
  // Re-mounting on key change replays the one-shot cascade.
  const [take, setTake] = useState(0)

  return (
    <div className="w-full max-w-md">
      <m.div
        key={take}
        className="rounded-card border border-edge bg-surface p-5"
        variants={cascade}
        initial="hidden"
        animate="show"
      >
        <m.p variants={row} className="flex items-start gap-3 text-sm text-fg-muted">
          <span aria-hidden className="mt-1 size-2 shrink-0 animate-pulse rounded-full bg-accent" />
          <span>“Bench three by eight at one-thirty-five… slept six hours, knee felt tweaky.”</span>
        </m.p>

        <m.div variants={row} aria-hidden className="my-4 h-px bg-edge" />

        <dl className="flex flex-col gap-2.5">
          {EXTRACTED.map((entry) => (
            <m.div key={entry.label} variants={row} className="flex items-baseline justify-between gap-4">
              <dt className="font-mono text-xs tracking-wider text-fg-muted uppercase">{entry.label}</dt>
              <dd className="font-mono text-sm text-fg tabular-nums slashed-zero">{entry.value}</dd>
            </m.div>
          ))}
        </dl>

        <m.p variants={row} className="mt-4 border-t border-edge pt-4 text-sm leading-relaxed text-fg">
          <span className="font-mono text-xs tracking-wider text-accent uppercase">Bill · </span>
          Third week at 135 — Friday we try 140. Easy on the knee today: I remember last time.
        </m.p>
      </m.div>

      <button
        type="button"
        onClick={() => setTake((t) => t + 1)}
        className="mt-3 cursor-pointer font-mono text-xs text-fg-muted transition-colors duration-150 hover:text-fg"
      >
        ↺ replay
      </button>
    </div>
  )
}
