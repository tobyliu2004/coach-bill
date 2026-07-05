# Design rules (frontend)

Apply to **every** screen, no exceptions — consistency IS the premium feel. Values live in
`frontend/src/index.css` (the token file); this doc is the why + the rules. Dark-only: dark
is the brand, not a mode. (Semantic tokens keep a future light theme cheap.)

> ⏳ = pending the by-eye pick (issue #9 checkpoint); update this doc when locked.

## Color
- Screens are ~95% ink + white. **Exactly two text colors**: `text-fg` (near-white) and
  `text-fg-muted` (the one gray). Never invent a third gray.
- **Exactly three surfaces**: `bg-bg` (page) → `bg-surface` (card) → `bg-raised` (popover).
  Elevation = surface step + hairline border (`border-edge`), **never drop shadows** on dark.
- **One accent** (⏳ amber vs volt), ≤5% of any screen: primary CTA, the key stat, one moment
  of energy. `accent-hot` for large type/CTAs only (contrast is AA-large, not AA-small).
  Everything else stays grayscale — data earns color, decoration doesn't.
- Gradients are **light, not paint**: `.spotlight` glow is the only sanctioned gradient.
- `.grain` overlay on full-page surfaces (kills banding; 3.5% opacity, no higher).
- New colors require updating the token file + this doc — never inline hex in components.

## Typography
- Fonts (⏳ Archivo-wide + IBM Plex Mono vs Geist + Geist Mono): `font-display` for
  headlines (wide stance via `--display-width`), `font-sans` body, `font-mono` for ALL data.
- Display sizes only via `text-display` / `text-display-sm` — tracking (−0.028/−0.022em) and
  weight are baked into the tokens; never hand-set letter-spacing on headlines.
- Hierarchy comes from **size jumps and color** (fg vs fg-muted), not extra weights.
  Weights in use: 400–600. Nothing bolder except the wordmark.
- **Every number the user reads is data**: `font-mono tabular-nums` (add `slashed-zero` where
  0/O confusion matters). Weights, reps, calories, dates, timers — no exceptions. Digits must
  align in columns and not jiggle when they change.

## Shape & space
- **Two radii only**: `rounded-control` (6px — buttons, inputs, chips) and `rounded-card`
  (10px — cards, panels). Never `rounded-2xl`+, never mixed radii in one component.
- 4/8px spacing grid. Components are compact (8–12px padding); *sections* are airy
  (py-24+ on landing). Density inside, air between — that contrast is the look.
- Borders: `border-edge` default, `border-edge-strong` for interactive affordance.

## Motion
- Everything < 300ms; micro-interactions 150–250ms; in-app feedback ≤ 100ms.
- Default easing `ease-out-expo`. Never ease-in for entrances; springs only for gestures,
  critically damped (no wobble).
- Animate **transform and opacity only** — never height/margin/padding (jank on mid phones).
- Entrances: fade + 8–16px rise, 30–60ms stagger, one-shot. Menus scale from their trigger.
- **Repeated actions never animate** (logging a set must feel instant). One signature motion
  moment per page, max.
- All motion goes through Motion's `<MotionConfig reducedMotion="user">` (already at the app
  root) so `prefers-reduced-motion` degrades to fades automatically. Don't bypass it with
  raw CSS animation on meaningful content.

## Signature moments (landing/marketing surfaces)
- Every marketing page earns exactly ONE signature element (currently: the data athlete —
  real lifter photographs rendered as luminance-driven glyph scanlines in `DataAthlete.tsx`,
  cycling lifts torn apart by wind gusts). Spend all boldness there; keep everything around
  it quiet. Pose sources: photos with a lit subject, segmented offline (rembg), flattened
  onto true black.
- Scroll storytelling uses the pinned-chapter pattern (`CheckInChapter.tsx`): tall section
  (~300vh) + `sticky top-0 h-dvh` stage + scroll-progress-driven beats via Motion
  `useScroll`; per-frame updates are imperative DOM writes (MotionValues), never React
  re-renders.
- Lenis provides smooth scroll on marketing pages only — never in the daily app. Disabled
  under reduced motion.
- Canvas rules: 2D only (no WebGL), devicePixelRatio capped at 2, pause when offscreen/tab
  hidden, reduced-motion renders a meaningful static frame (not blank).

## Never (the AI-slop ban list)
- Purple/indigo gradients; any gradient as background paint; colored glows around cards.
- Glassmorphism (`backdrop-blur` cards), colored card edge-stripes, floating blurred orbs.
- Centered badge-pill-over-headline hero; three icon-topped feature cards in a row.
- Emoji as icons or bullets. Icons come from one stroke-consistent set (Lucide) or nowhere.
- Inline styles for anything a token covers; arbitrary Tailwind values (`text-[17px]`) when
  a token exists.
- Vague copy ("Build the future"). Copy is specific: "3×8 @ 135 — logged in nine seconds."
