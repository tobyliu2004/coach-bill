import { useEffect } from 'react'
import Lenis from 'lenis'
import 'lenis/dist/lenis.css'
import { LazyMotion, MotionConfig, domAnimation, useReducedMotion } from 'motion/react'
import * as m from 'motion/react-m'
import { CheckInChapter } from './components/CheckInChapter'
import { DataAthlete } from './components/DataAthlete'
import { HealthDot } from './components/HealthDot'

/** Site-wide motion defaults: expo-out, <300ms class, reduced-motion → fades. */
const EASE_OUT_EXPO = [0.16, 1, 0.3, 1] as const

const HEADLINE_LINES = ['The coach who', 'remembers', 'every rep.'] as const

/** Load choreography: nav → headline lines unmask → sub/CTA → voice strip. */
function lineDelay(i: number): number {
  return 0.15 + i * 0.09
}

function SmoothScroll() {
  const reduced = useReducedMotion()
  useEffect(() => {
    if (reduced) return
    const lenis = new Lenis({ autoRaf: true })
    return () => lenis.destroy()
  }, [reduced])
  return null
}

function App() {
  return (
    <MotionConfig reducedMotion="user" transition={{ duration: 0.35, ease: EASE_OUT_EXPO }}>
      <LazyMotion features={domAnimation} strict>
        <SmoothScroll />
        <div aria-hidden className="grain" />

        <header className="fixed inset-x-0 top-0 z-40 border-b border-edge bg-bg/90">
          <m.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-4"
          >
            <span className="font-display text-lg font-semibold tracking-tight text-fg">Coach Bill</span>
            <span className="font-mono text-xs tracking-wider text-fg-muted uppercase">First month free</span>
          </m.div>
        </header>

        {/* ---- Hero: thesis + the data athlete ---- */}
        <section className="spotlight relative flex h-dvh min-h-[640px] flex-col overflow-hidden">
          {/* The signature: a life-size athlete made of data, cycling lifts. */}
          <m.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 1.4, delay: 0.7 }}
            className="absolute inset-0 opacity-55 md:opacity-100"
          >
            <DataAthlete className="h-full w-full" />
          </m.div>

          <div className="relative z-10 mx-auto flex w-full max-w-6xl flex-1 flex-col justify-center px-6 pt-24">
            <h1 className="font-display text-hero text-balance text-fg">
              {HEADLINE_LINES.map((line, i) => (
                <span key={line} className="mask-line">
                  <m.span
                    className="block"
                    initial={{ y: '110%' }}
                    animate={{ y: 0 }}
                    transition={{ duration: 0.7, ease: EASE_OUT_EXPO, delay: lineDelay(i) }}
                  >
                    {i === HEADLINE_LINES.length - 1 ? (
                      <>
                        <span className="text-accent">every rep</span>.
                      </>
                    ) : (
                      line
                    )}
                  </m.span>
                </span>
              ))}
            </h1>

            <m.div
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, ease: EASE_OUT_EXPO, delay: 0.55 }}
              className="mt-8 flex flex-col gap-6 md:flex-row md:items-center md:gap-10"
            >
              <a
                href="#signup"
                className="inline-block w-fit rounded-control bg-accent px-5 py-2.5 text-sm font-semibold text-accent-ink transition-transform duration-150 ease-out-expo hover:scale-[1.02] active:scale-[0.98]"
              >
                Start your free month
              </a>
              <p className="max-w-md text-base leading-relaxed text-fg-muted">
                Say your check-in out loud. Bill logs it, tracks it, and coaches you like
                he&rsquo;s known you for months — because he has.
              </p>
            </m.div>
          </div>

          <div className="relative z-10 mx-auto w-full max-w-6xl px-6 pb-5">
            <p className="font-mono text-xs tracking-widest text-fg-muted uppercase">
              Scroll — one check-in, start to coached
            </p>
          </div>
        </section>

        {/* ---- Chapter: 01 speak → 02 logged → 03 coached ---- */}
        <CheckInChapter />

        {/* ---- End cap: free-trial signup (wired for real in the Auth issue) ---- */}
        <section id="signup" className="relative">
          <div className="mx-auto flex w-full max-w-6xl flex-col items-start gap-6 px-6 py-28">
            <h2 className="font-display text-display-sm text-balance text-fg">
              Your first month is free.
            </h2>
            <p className="max-w-md text-base leading-relaxed text-fg-muted">
              No card, no catch — talk to Bill for a month and see what a coach with a
              perfect memory feels like.
            </p>
            <div className="flex flex-col gap-3 sm:flex-row">
              <a
                href="#signup"
                className="inline-block rounded-control bg-accent px-5 py-2.5 text-center text-sm font-semibold text-accent-ink transition-transform duration-150 ease-out-expo hover:scale-[1.02] active:scale-[0.98]"
              >
                Start free month
              </a>
              <a
                href="#signup"
                className="inline-block rounded-control border border-edge-strong px-5 py-2.5 text-center text-sm font-semibold text-fg transition-colors duration-150 hover:border-fg-muted"
              >
                Continue with Google
              </a>
            </div>
          </div>
          <footer className="border-t border-edge">
            <div className="mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-4">
              <span className="font-mono text-xs text-fg-muted">© 2026 Coach Bill</span>
              <HealthDot />
            </div>
          </footer>
        </section>

      </LazyMotion>
    </MotionConfig>
  )
}

export default App
