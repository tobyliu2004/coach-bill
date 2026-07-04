import { LazyMotion, MotionConfig, domAnimation } from 'motion/react'
import * as m from 'motion/react-m'
import { CheckInDemo } from './components/CheckInDemo'
import { HealthDot } from './components/HealthDot'
import { VariantSwitcher } from './components/VariantSwitcher'

/** Site-wide motion defaults: expo-out, <300ms class, reduced-motion → fades. */
const EASE_OUT_EXPO = [0.16, 1, 0.3, 1] as const

const rise = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0 },
} as const

function App() {
  return (
    <MotionConfig reducedMotion="user" transition={{ duration: 0.35, ease: EASE_OUT_EXPO }}>
      <LazyMotion features={domAnimation} strict>
        <div aria-hidden className="grain" />
        <div className="spotlight flex min-h-svh flex-col">
          <header className="mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-5">
            <span className="font-display text-lg font-semibold tracking-tight text-fg">Coach Bill</span>
            <span className="font-mono text-xs tracking-wider text-fg-muted uppercase">Private beta</span>
          </header>

          <main className="mx-auto flex w-full max-w-6xl flex-1 items-center px-6 py-16 md:py-24">
            <div className="grid w-full items-center gap-12 lg:grid-cols-[1.1fr_1fr]">
              <m.section
                initial="hidden"
                animate="show"
                variants={{ show: { transition: { staggerChildren: 0.06 } } }}
              >
                <m.h1 variants={rise} className="font-display text-display-sm text-balance text-fg md:text-display">
                  The coach who remembers <span className="text-accent">every rep</span>.
                </m.h1>
                <m.p variants={rise} className="mt-6 max-w-xl text-base leading-relaxed text-fg-muted md:text-lg">
                  Say your check-in out loud. Bill logs the sets, tracks the trends, and answers
                  like he&rsquo;s known you for months — because he has.
                </m.p>
                <m.div variants={rise} className="mt-8">
                  <a
                    href="mailto:tobyliu2004@gmail.com?subject=Coach%20Bill%20early%20access"
                    className="inline-block rounded-control bg-accent px-5 py-2.5 text-sm font-semibold text-accent-ink transition-transform duration-150 ease-out-expo hover:scale-[1.02] active:scale-[0.98]"
                  >
                    Get early access
                  </a>
                </m.div>
              </m.section>

              <div className="justify-self-start lg:justify-self-end">
                <CheckInDemo />
              </div>
            </div>
          </main>

          <footer className="border-t border-edge">
            <div className="mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-4">
              <span className="font-mono text-xs text-fg-muted">© 2026 Coach Bill</span>
              <HealthDot />
            </div>
          </footer>
        </div>
        <VariantSwitcher />
      </LazyMotion>
    </MotionConfig>
  )
}

export default App
