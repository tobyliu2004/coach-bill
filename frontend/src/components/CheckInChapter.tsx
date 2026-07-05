import { useRef } from 'react'
import { useMotionValueEvent, useReducedMotion, useScroll, useTransform } from 'motion/react'
import * as m from 'motion/react-m'

/**
 * Scroll-scrubbed story chapter (the meuze pattern): a tall section with a
 * sticky full-height stage; scroll progress drives three beats —
 * 01 SPEAK (transcript types) → 02 LOGGED (rows extract, numbers roll) →
 * 03 COACHED (Bill replies). All per-frame updates are imperative DOM writes
 * via MotionValues; React never re-renders during scroll.
 */

const TRANSCRIPT = '“Hey Coach, here’s my numbers for the day — bench one-thirty-five, military press one-oh-five, squats two-twenty-five…”'
const REPLY = 'Logged. Bench moved fast today — we go 140 next session. Press is stalling at 105, so Friday we add a back-off set. Squats looked strong.'

const BEATS = [
  { n: '01', label: 'speak' },
  { n: '02', label: 'logged' },
  { n: '03', label: 'coached' },
] as const

const ROWS = [
  { label: 'bench press', value: (t: number) => `${Math.round(135 * t)} lb · 4×8` },
  { label: 'military press', value: (t: number) => `${Math.round(105 * t)} lb · 3×6` },
  { label: 'squat', value: (t: number) => `${Math.round(225 * t)} lb · 5×5` },
] as const

function clamp01(v: number): number {
  return Math.min(1, Math.max(0, v))
}
function smoothstep(t: number): number {
  const c = clamp01(t)
  return c * c * (3 - 2 * c)
}
/** Progress of `p` remapped to the [a, b] window. */
function window01(p: number, a: number, b: number): number {
  return clamp01((p - a) / (b - a))
}

export function CheckInChapter() {
  const sectionRef = useRef<HTMLElement | null>(null)
  const transcriptRef = useRef<HTMLParagraphElement | null>(null)
  const replyRef = useRef<HTMLSpanElement | null>(null)
  const replyBlockRef = useRef<HTMLParagraphElement | null>(null)
  const rowRefs = useRef<Array<HTMLDivElement | null>>([])
  const valueRefs = useRef<Array<HTMLElement | null>>([])
  const beatRefs = useRef<Array<HTMLLIElement | null>>([])
  const railRef = useRef<HTMLDivElement | null>(null)
  const numeralRef = useRef<HTMLSpanElement | null>(null)
  const reduced = useReducedMotion()

  const { scrollYProgress } = useScroll({
    target: sectionRef,
    offset: ['start start', 'end end'],
  })

  useMotionValueEvent(scrollYProgress, 'change', (p) => {
    // Reduced motion: show the finished state, no scrubbing.
    const done = reduced ? 1 : p

    // Beat 01 — transcript types with scroll (0.05 → 0.35).
    const typed = smoothstep(window01(done, 0.05, 0.35))
    if (transcriptRef.current) {
      const chars = Math.round(TRANSCRIPT.length * typed)
      transcriptRef.current.textContent = TRANSCRIPT.slice(0, chars) || ' '
    }

    // Beat 02 — rows wipe in sequentially, numbers roll (0.38 → 0.62).
    ROWS.forEach((row, i) => {
      const t = smoothstep(window01(done, 0.38 + i * 0.07, 0.5 + i * 0.07))
      const el = rowRefs.current[i]
      if (el) {
        el.style.opacity = String(t)
        el.style.transform = `translateY(${(1 - t) * 14}px)`
      }
      const val = valueRefs.current[i]
      if (val) val.textContent = t > 0 ? row.value(t) : ''
    })

    // Beat 03 — Bill's block surfaces, then types back (0.66 → 0.9).
    const surfaced = smoothstep(window01(done, 0.63, 0.68))
    if (replyBlockRef.current) {
      replyBlockRef.current.style.opacity = String(surfaced)
      replyBlockRef.current.style.transform = `translateY(${(1 - surfaced) * 14}px)`
    }
    const replied = smoothstep(window01(done, 0.66, 0.9))
    if (replyRef.current) {
      const chars = Math.round(REPLY.length * replied)
      replyRef.current.textContent = REPLY.slice(0, chars)
    }

    // Progress rail + beat labels + the giant ghost numeral.
    if (railRef.current) railRef.current.style.transform = `scaleY(${clamp01(done / 0.9)})`
    const active = done < 0.38 ? 0 : done < 0.66 ? 1 : 2
    beatRefs.current.forEach((li, i) => {
      if (!li) return
      li.style.opacity = i === active ? '1' : '0.35'
      li.style.color = i === active ? 'var(--accent)' : 'var(--fg-muted)'
    })
    if (numeralRef.current && numeralRef.current.textContent !== BEATS[active]!.n) {
      numeralRef.current.textContent = BEATS[active]!.n
    }
  })

  // Stage entrance (one-shot, not scrubbed).
  const stageOpacity = useTransform(scrollYProgress, [0, 0.04], [0.4, 1])

  return (
    <section ref={sectionRef} data-tone="raised" className="relative h-[320vh] bg-surface">
      <div className="spotlight sticky top-0 flex h-dvh items-center overflow-hidden">
        {/* Giant ghost numeral — the chapter counter at cinematic scale. */}
        <span
          ref={numeralRef}
          aria-hidden
          className="font-display pointer-events-none absolute top-1/2 right-0 -translate-y-1/2 text-[42vh] leading-none font-semibold text-fg opacity-[0.045] select-none md:right-8"
        >
          01
        </span>

        <div className="relative mx-auto grid w-full max-w-6xl gap-10 px-6 md:grid-cols-[auto_1fr] md:gap-16">
          {/* Beat rail — a true sequence, so the numbering carries information. */}
          <div className="flex gap-6 md:flex-col md:justify-center">
            <div aria-hidden className="relative hidden w-0.5 bg-edge md:block">
              <div
                ref={railRef}
                className="absolute inset-x-0 top-0 h-full origin-top bg-accent"
                style={{ transform: 'scaleY(0)' }}
              />
            </div>
            <ol className="flex gap-6 font-mono text-xs tracking-widest uppercase md:flex-col md:gap-10">
              {BEATS.map((b, i) => (
                <li
                  key={b.n}
                  ref={(el) => {
                    beatRefs.current[i] = el
                  }}
                  className="transition-colors duration-300"
                  style={{ opacity: i === 0 ? 1 : 0.35 }}
                >
                  <span className="tabular-nums">{b.n}</span>
                  <span className="ml-2">{b.label}</span>
                </li>
              ))}
            </ol>
          </div>

          <m.div style={{ opacity: stageOpacity }} className="max-w-3xl">
            <p className="font-mono text-xs tracking-widest text-fg-muted uppercase">
              One check-in, thirty seconds
            </p>

            <p
              ref={transcriptRef}
              className="font-display text-display-sm mt-6 min-h-40 text-balance text-fg md:min-h-48"
            >
              {' '}
            </p>

            <dl className="mt-8 flex flex-col gap-4 border-t border-edge pt-6">
              {ROWS.map((row, i) => (
                <div
                  key={row.label}
                  ref={(el) => {
                    rowRefs.current[i] = el
                  }}
                  className="flex items-baseline justify-between gap-4 border-b border-edge pb-4 last:border-b-0"
                  style={{ opacity: 0 }}
                >
                  <dt className="font-mono text-xs tracking-wider text-fg-muted uppercase">
                    {row.label}
                  </dt>
                  <dd
                    ref={(el) => {
                      valueRefs.current[i] = el
                    }}
                    className="font-mono text-lg text-fg tabular-nums slashed-zero md:text-2xl"
                  />
                </div>
              ))}
            </dl>

            <p
              ref={replyBlockRef}
              className="mt-8 text-lg leading-relaxed text-fg md:text-xl"
              style={{ opacity: 0 }}
            >
              <span className="font-mono text-sm tracking-wider text-accent uppercase">Bill · </span>
              <span ref={replyRef} />
              <span aria-hidden className="cursor-blink ml-0.5 inline-block h-[1.1em] w-[2px] translate-y-[2px] bg-accent" />
            </p>
          </m.div>
        </div>
      </div>
    </section>
  )
}
