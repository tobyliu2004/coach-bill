/**
 * Landing copy that more than one surface renders.
 *
 * The hero headline lives here, not in `Landing.tsx`, because the social card
 * (`src/dev/OgCard.tsx`) has to render the SAME headline. A card advertising a
 * headline the page no longer has is invisible drift: the card never renders
 * on the site, so nobody sees it go stale — only the people it was shared with.
 *
 * It's a module rather than an export from `Landing.tsx` because a component
 * file that also exports constants loses Fast Refresh.
 */
export const HEADLINE_LINES = ['The coach who', 'remembers', 'every rep.'] as const
