import { DataAthlete } from '../components/DataAthlete'
import { HEADLINE_LINES } from '../lib/landingCopy'

/**
 * Dev-only artboards for the two images that ship in `frontend/public/`:
 * the social preview card and the iOS touch icon.
 *
 * These are not mocks — they are the REAL components with the REAL fonts and
 * the REAL tokens, rendered at fixed pixel sizes and screenshotted. That's the
 * whole point: a hand-drawn card drifts from the site the moment the site
 * changes, and a card generated from `index.css` cannot.
 *
 * **The regeneration recipe lives in `design/og/README.md` and only there.**
 * An earlier copy of the commands sat here too and had already drifted — it
 * omitted the downsample step, so following it produced a 2400×1260 card while
 * `index.html` kept declaring `og:image:width` 1200. One source, not two.
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

      {/* The headline comes FROM Landing, line for line — copy-pasting it here
          would let the card advertise a headline the page no longer has, and
          nobody would notice, because the card never renders on the site. */}
      <div className="relative z-10 flex flex-col gap-9 pl-20">
        <span className="font-display text-lg font-semibold tracking-tight text-fg">Coach Bill</span>
        <h1 className="font-display text-hero text-fg">
          {HEADLINE_LINES.map((line, i) => (
            <span key={line} className="block">
              {i === HEADLINE_LINES.length - 1 ? (
                // Last line carries the one accent, minus its full stop.
                <>
                  <span className="text-accent">{line.replace(/\.$/, '')}</span>.
                </>
              ) : (
                line
              )}
            </span>
          ))}
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
