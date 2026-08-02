import type { Bar } from '../lib/trends'

/**
 * A bar chart, hand-rolled in inline SVG. No charting library.
 *
 * This component is DUMB: every coordinate it draws was computed by `barLayout` in
 * lib/trends.ts and is tested there. It owns no arithmetic, no scale, and no clock — it
 * turns geometry into rects. That is the same split `checkInView.ts`/`history.ts` already
 * established, and the reason is unchanged: vitest here is node-env with no DOM, so anything
 * that lives in a `.tsx` is reviewed by eye rather than asserted.
 *
 * THIS FILE SETS THE INLINE-SVG CONVENTION for the app, since there was no prior art:
 *   * a fixed `viewBox="0 0 100 100"` matching lib/trends.ts's coordinate space, so the
 *     geometry is resolution-independent and nothing ever measures the DOM;
 *   * `preserveAspectRatio="none"` because this chart is meant to stretch to its container —
 *     the x axis is time and the y axis is tonnage, so their aspect ratio is not meaningful;
 *   * `role="img"` + `aria-label`, because a screen reader gets nothing from a pile of
 *     rects. The label carries the actual summary, not "bar chart";
 *   * every axis or value label is `font-mono tabular-nums` (design.md: every number the
 *     user reads is data), so the labels don't jiggle as the window changes.
 *
 * Amber is spent HERE and nowhere else on the screen: design.md caps the accent at ~5% and
 * says data earns color. The bars are the one thing on /trends worth looking at first.
 */

interface BarChartProps {
  bars: Bar[]
  /** Days that were trained but carry no measurable tonnage — drawn as baseline ticks. */
  marks?: Bar[]
  label: string
}

export function BarChart({ bars, marks = [], label }: BarChartProps) {
  return (
    <svg
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      role="img"
      aria-label={label}
      className="h-40 w-full"
    >
      {bars.map((bar) => (
        <rect
          key={bar.date}
          // The 0.14/0.72 inset is PRESENTATION, not data: it puts a hairline gap between
          // adjacent days so 30 bars read as 30 bars. The value itself is `bar.height`,
          // untouched — nothing here rescales what lib/trends.ts computed.
          x={bar.x + bar.width * 0.14}
          y={bar.y}
          width={bar.width * 0.72}
          height={bar.height}
          className="fill-accent"
        />
      ))}
      {marks.map((mark) => (
        // A day with only unweighted work has no tonnage to plot — `volume_kg` is null, not
        // 0 (backend AC row 11). Dropping it entirely would make a day the user trained look
        // identical to a day they did nothing, so it gets a baseline tick instead: present,
        // but explicitly not a quantity. Muted, because it is not a measurement.
        <rect
          key={mark.date}
          x={mark.x + mark.width * 0.14}
          y={98}
          width={mark.width * 0.72}
          height={2}
          className="fill-fg-muted"
        />
      ))}
    </svg>
  )
}
