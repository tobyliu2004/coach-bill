import { DataAthlete } from '../components/DataAthlete'

/**
 * Dev-only artboards for the two images that ship in `frontend/public/`:
 * the social preview card and the iOS touch icon.
 *
 * These are not mocks — they are the REAL components with the REAL fonts and
 * the REAL tokens, rendered at fixed pixel sizes and screenshotted. That's the
 * whole point: a hand-drawn card drifts from the site the moment the site
 * changes, and a card generated from `index.css` cannot.
 *
 *   npm run dev
 *   design/capture.sh 'http://127.0.0.1:5173/__og?target=og&scene=deadlift' \
 *     frontend/public/og.png 1200 630 2
 *   design/capture.sh 'http://127.0.0.1:5173/__og?target=icon' \
 *     frontend/public/apple-touch-icon.png 180 180 1
 *
 * `&scene=deadlift` is DataAthlete's existing dev freeze — it pins the pose so
 * two captures a week apart produce the same lifter. The route is mounted only
 * under `import.meta.env.DEV` (AppRoutes), so this never reaches production.
 */

/** 1200×630 — the size every scraper crops from. */
function SocialCard() {
  return (
    <div className="spotlight relative flex h-[630px] w-[1200px] items-center overflow-hidden bg-bg">
      <div aria-hidden className="grain" />

      {/* The signature, frozen: the same glyph-scanline lifter as the hero.
          Full-bleed, not boxed to the right half — DataAthlete places the
          figure right-of-center itself, and its ambient filler particles need
          the whole width or the figure sits in a visible rectangle. */}
      <div aria-hidden className="absolute inset-0">
        <DataAthlete className="h-full w-full" />
      </div>

      {/* The three lines are explicit, exactly as Landing's HEADLINE_LINES
          renders them — a card that re-wraps differently from the page it
          links to reads as a different product. */}
      <div className="relative z-10 flex flex-col gap-9 pl-20">
        <span className="font-display text-lg font-semibold tracking-tight text-fg">Coach Bill</span>
        <h1 className="font-display text-hero text-fg">
          <span className="block">The coach who</span>
          <span className="block">remembers</span>
          <span className="block">
            <span className="text-accent">every rep</span>.
          </span>
        </h1>
        <p className="font-mono text-sm tracking-widest text-fg-muted uppercase">
          Type one sentence — logged, tracked, coached
        </p>
      </div>
    </div>
  )
}

/**
 * 180×180 — iOS masks and rounds this itself, so it ships square, full-bleed
 * and fully opaque. It exists because `favicon.svg` renders its "CB" in
 * `system-ui`; on the home screen we want the real display face.
 */
function TouchIcon() {
  return (
    <div className="flex h-[180px] w-[180px] items-center justify-center bg-bg">
      <span className="font-display text-[68px] font-semibold tracking-tight text-accent">CB</span>
    </div>
  )
}

export default function OgCard() {
  const target = new URLSearchParams(window.location.search).get('target')
  return target === 'icon' ? <TouchIcon /> : <SocialCard />
}
