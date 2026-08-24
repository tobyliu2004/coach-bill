/**
 * The loading placeholder every list screen shares — /app, /history, /trends.
 *
 * WHY THIS EXISTS AT ALL (#43). All three screens compute a `loading` state and, until this
 * commit, rendered nothing for it. A slow fetch was pixel-identical to "you have nothing
 * logged", which is the same failure-reads-as-benign shape as #18, #39 and #46: the decision
 * layer keeps the states distinct, the presentation layer collapses them back together.
 *
 * WHY ONE COMPONENT AND NOT THREE. Three hand-rolled placeholder blocks is how three screens
 * drift into three different loading experiences — and #43 exists precisely because the
 * two-screen version of this bug was fixed on neither screen. The `data-component` marker
 * below is what makes "they all use the same one" an assertion a test can make (#43 row 20)
 * rather than a promise in a comment.
 *
 * WHY NOTHING SHIMMERS. design.md: repeated actions never animate, and motion is transform
 * and opacity only. A pulsing skeleton on the screen you open twenty times a day is exactly
 * the fidget the rule bans — and a shimmer is an animated background/gradient, which is
 * banned twice over. Static blocks in the card surface read as "this is where the cards go"
 * without asking for attention. No new colors: `bg-surface` + `border-edge` are the same card
 * treatment the real rows use, so the placeholder is literally the shape of what replaces it.
 *
 * ACCESSIBILITY. `role="status"` (a polite live region) plus a real `aria-label`, so a screen
 * reader is told what is loading rather than meeting an anonymous grey box. The label is
 * required, not optional — an unnamed status region is the a11y equivalent of the bug this
 * component was written to fix.
 */

/** The card block itself: the same surface, border and radius as the rows it stands in for. */
const BLOCK = 'rounded-card border border-edge bg-surface'

/**
 * How tall each block is, sized to the content it replaces:
 *   'row'   — a check-in card (text + its timestamp)
 *   'panel' — a chart panel on the trends dashboard
 */
const HEIGHTS = {
  row: 'h-20',
  panel: 'h-40',
} as const

export interface SkeletonProps {
  /** What is loading — becomes the live region's accessible name. Be specific (design.md). */
  label: string
  /** How many blocks to stand in for. Enough to read as a list, never a full fake page. */
  count?: number
  /** Which block size this screen's content wants. */
  shape?: keyof typeof HEIGHTS
}

export function Skeleton({ label, count = 3, shape = 'row' }: SkeletonProps) {
  return (
    <div
      role="status"
      aria-label={label}
      data-component="skeleton"
      className="flex flex-col gap-2"
    >
      {Array.from({ length: count }, (_, i) => (
        // Index keys are correct here and only here: these blocks are identical, ordered, and
        // never reordered or keyed to data — there is no identity for React to preserve.
        <div key={i} className={`${BLOCK} ${HEIGHTS[shape]}`} />
      ))}
    </div>
  )
}
