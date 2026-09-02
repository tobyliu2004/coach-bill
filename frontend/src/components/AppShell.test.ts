/**
 * Oracle tripwire for issue #51, AC row 25 — "AppShell at 375px wide: all five nav items
 * reachable; nothing overflows the viewport."
 *
 * Part of commit #1 on `feat/plan-and-diet`, written BEFORE any implementation exists.
 *
 * ===== WHAT THIS TEST DOES NOT PROVE =====
 *
 * It does NOT prove row 25. vitest here is node env with no DOM and no layout engine
 * (vite.config.ts), so nothing in this repo can measure a rendered header against a 375px
 * viewport. "Nothing overflows" is a LAYOUT fact, and this file cannot observe layout.
 *
 * THE REAL PROOF OF ROW 25 IS THE ON-SCREEN E2E AT 375px IN PHASE 6. This file is a
 * tripwire that fails the two ways row 25 has already failed in this product:
 *   1. a nav item exists as a route but is missing from the shell, so nobody finds it —
 *      /history and /trends were live on coachbill.fit for weeks and Toby never found them,
 *      which the issue names as a DISCOVERY failure;
 *   2. the header is still a single unbreakable flex row — the concrete cause the issue
 *      identifies: "one flex row with no responsive variant at any breakpoint", now holding
 *      a wordmark, FIVE links, an email and a sign-out button.
 * A green tripwire means neither of those two is true. It does not mean the header fits.
 *
 * ===== WHY IT READS ONE NAMED FILE AND NEVER GLOBS =====
 *
 * #48's row 22 grepped `frontend/src/` for a string — and vitest colocates tests in that
 * same tree, so the assertion matched its OWN oracle file and the branch could never go
 * green. This file imports the single named module `./AppShell.tsx` as raw text. It scans
 * nothing else, and it can never scan itself.
 *
 * ===== IF THE BUILD MOVES THE NAV LIST OUT OF THE SHELL =====
 *
 * If the implementation extracts the destinations into a `NAV_ITEMS` export that AppShell
 * renders, the literals below leave AppShell.tsx and this test goes red for a reason that
 * is not a bug. That is a CORRECTNESS-TABLE AMENDMENT for Toby to record on issue #51 —
 * not a quiet patch to this file. (My recommendation, in the report: that module is the
 * better home, because a list of destinations is a decision and decisions belong in `lib/`.)
 */
import { describe, expect, it } from 'vitest'
import appShellSource from './AppShell.tsx?raw'

/** The five screens an onboarded user moves between once /plan and /diet exist. */
const NAV_DESTINATIONS = ['/app', '/history', '/trends', '/plan', '/diet']

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * Does `region` link to `path`?
 *
 * The path must appear as a COMPLETE quoted string literal. A bare `includes('/plan')` would
 * match `href="/planner"`, and prose in a comment — the `interested`/`rest` bug, which cost
 * this repo two tests that could not fail.
 */
function linksTo(region: string, path: string): boolean {
  return new RegExp(`["'\`]${escapeRegExp(path)}["'\`]`).test(region)
}

/**
 * A Tailwind class token starts at the start of the string or after whitespace, a quote or
 * a brace — never mid-word.
 */
const TOKEN_START = `(?:^|[\\s"'\`{(])`
const TOKEN_END = `(?:$|[\\s"'\`)}])`

/**
 * A responsive VARIANT PREFIX: `md:`, `lg:hidden`, `@md:flex` (container query). The colon
 * is what makes it a breakpoint rather than a word — `text-sm` is not responsive.
 */
const RESPONSIVE_VARIANT = new RegExp(`${TOKEN_START}@?(?:sm|md|lg|xl|2xl):`)

/**
 * A utility that lets a row REFLOW instead of overflowing: wrap it, stack it, grid it, or
 * scroll it. Described as a category (any of these mechanisms) rather than as the one class
 * string I happen to expect — the repo's recurring bug is enumerating instead of describing.
 */
const REFLOW_UTILITY = new RegExp(
  `${TOKEN_START}(?:flex-wrap|flex-wrap-reverse|flex-col|grid|overflow-x-auto|overflow-x-scroll|overflow-auto)${TOKEN_END}`,
)

/** Can this markup reflow on a narrow screen at all — by wrapping, stacking or breakpoint? */
function canReflow(region: string): boolean {
  return RESPONSIVE_VARIANT.test(region) || REFLOW_UTILITY.test(region)
}

/**
 * The chrome that has to survive 375px: everything between `<header` and `</header>`.
 *
 * Scoped to the header on purpose. The shell's OUTER element already carries
 * `flex min-h-dvh flex-col` — a whole-file scan would match that and go green without a
 * single thing changing about the row that actually overflows. An oracle that passes
 * against the unfixed file is not an oracle.
 */
function headerRegion(source: string): string {
  const start = source.indexOf('<header')
  const end = source.indexOf('</header>')
  if (start === -1 || end === -1 || end <= start) return ''
  return source.slice(start, end)
}

describe('the tripwire matchers themselves (self-test, both directions)', () => {
  it('recognises a link to a path, and only to that exact path', () => {
    expect(linksTo('<AppNavLink to="/plan">Plan</AppNavLink>', '/plan')).toBe(true)
    expect(linksTo("<NavLink to='/diet' />", '/diet')).toBe(true)
    expect(linksTo('<NavLink to={`/plan`} />', '/plan')).toBe(true)

    // Must FAIL: a longer path that merely starts with it...
    expect(linksTo('<a href="/planner">Planner</a>', '/plan')).toBe(false)
    // ...and prose that names the screen without linking to it.
    expect(linksTo('// the /plan screen renders this', '/plan')).toBe(false)
  })

  it('recognises a reflow mechanism, and only a real one', () => {
    expect(canReflow('className="flex flex-wrap items-center gap-5"')).toBe(true)
    expect(canReflow('className="hidden md:flex"')).toBe(true)
    expect(canReflow('className="flex-col sm:flex-row"')).toBe(true)
    expect(canReflow('className="flex overflow-x-auto"')).toBe(true)
    expect(canReflow('className="@md:flex"')).toBe(true)

    // Must FAIL: today's header — one flex row, no wrap, no breakpoint. This is the exact
    // string row 25 exists to change, so if the matcher accepts it the matcher is broken.
    expect(
      canReflow('className="mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-4"'),
    ).toBe(false)
    // Must FAIL: 'wrap' as a SUBSTRING of another class — the `interested`/`rest` shape.
    expect(canReflow('className="flex-wrapper items-center"')).toBe(false)
    // Must FAIL: 'sm' with no colon is a size, not a breakpoint.
    expect(canReflow('className="text-sm text-fg-muted"')).toBe(false)
    expect(canReflow('className="flex items-center gap-5"')).toBe(false)
  })

  it('scopes to the header and excludes the page wrapper around it', () => {
    const fake = [
      '<div className="flex min-h-dvh flex-col">',
      '  <header className="border-b border-edge">',
      '    <nav className="flex items-center gap-5" />',
      '  </header>',
      '</div>',
    ].join('\n')

    // The wrapper's flex-col is outside the region...
    expect(headerRegion(fake)).not.toContain('min-h-dvh')
    // ...so it cannot make a non-reflowing header look like a reflowing one.
    expect(canReflow(headerRegion(fake))).toBe(false)
    expect(canReflow(fake)).toBe(true)
  })
})

describe('AppShell nav (AC row 25 — TRIPWIRE ONLY, see the file docstring)', () => {
  // AC row 25, the discovery half: "all five nav items reachable". A screen that ships
  // without a link to it is the failure this issue was opened over — /history and /trends
  // were live for weeks and unfound.
  it.each(NAV_DESTINATIONS)('links to %s from the app header', (path) => {
    expect(linksTo(headerRegion(appShellSource), path)).toBe(true)
  })

  // AC row 25, the overflow half — as far as a node-env test can honestly go. The issue
  // names the cause exactly: "a single flex row with no responsive variant at any
  // breakpoint". This asserts that is no longer true. It does NOT assert the result; the
  // on-screen E2E at 375px in Phase 6 does that.
  it('gives the header a way to reflow on a narrow screen', () => {
    expect(canReflow(headerRegion(appShellSource))).toBe(true)
  })

  // Guards the guard: if the header markers ever move or the file is renamed, the two tests
  // above would scan an empty string and could go quietly, wrongly red — or, worse, a future
  // edit to `headerRegion` could make them scan nothing and every `linksTo` would just fail
  // without saying why. This says why.
  it('found a header region to scan at all', () => {
    expect(headerRegion(appShellSource).length).toBeGreaterThan(0)
  })
})
