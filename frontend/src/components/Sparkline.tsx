/**
 * A sparkline — one series as a bare polyline, no axes, no grid, no labels of its own.
 *
 * Dumb, like `BarChart`: the `points` attribute was computed and tested by `sparkline` in
 * lib/trends.ts, and this component only draws it. Same inline-SVG convention as BarChart —
 * fixed `viewBox`, `preserveAspectRatio="none"`, `role="img"` + a real `aria-label`.
 *
 * Grayscale on purpose. design.md caps amber at ~5% of a screen and it is already spent on
 * the volume chart, which is the hero; three more accented lines would leave the screen with
 * no focal point at all. (`--color-ice` exists in the token file reserved for sleep/recovery
 * data — using it would be a token + design.md change, so v1 stays grayscale + amber.)
 *
 * `vectorEffect="non-scaling-stroke"` is load-bearing under `preserveAspectRatio="none"`:
 * without it the non-uniform scale stretches the stroke itself, so the line renders thick
 * horizontally and hairline vertically.
 *
 * An empty series yields `points=""`, which draws nothing — deliberately. The caller decides
 * what "no data" reads as; a chart that invents a flat line at zero would be claiming the
 * user weighed nothing.
 */

interface SparklineProps {
  /** An SVG `points` attribute from `sparkline()`. Never contains NaN — that is tested. */
  points: string
  label: string
}

export function Sparkline({ points, label }: SparklineProps) {
  return (
    <svg
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      role="img"
      aria-label={label}
      className="h-12 w-full"
    >
      <polyline
        points={points}
        fill="none"
        strokeWidth={1.5}
        vectorEffect="non-scaling-stroke"
        className="stroke-fg-muted"
      />
    </svg>
  )
}
