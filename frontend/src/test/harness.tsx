/**
 * Shared harness for the jsdom tier (`src/**\/*.test.tsx`).
 *
 * NOT a suite — the `dom` project only collects `*.test.tsx`, so this file is never run on
 * its own. It holds three things and nothing else:
 *
 *  1. a typed fake `AuthContext` value, so each test overrides only the field it is about;
 *  2. fixtures (a profile, a check-in, a trends payload) so no test has to invent API shapes;
 *  3. the CATEGORY RULES the #43/#46 contract is written in — "what is a state element",
 *     "what is the shared skeleton", "what counts as a visible treatment", "what counts as a
 *     way out". These live here ONCE on purpose: three copies of a rule is how three screens
 *     drift apart, which is the whole reason #43 exists.
 *
 * The rules are deliberately structural (an attribute, an ARIA role, an enabled control) and
 * never copy: a test that greps for the word "Nothing" breaks when someone rewords the empty
 * state and passes when a different screen happens to say it.
 *
 * Nothing here imports `lib/supabase` — that module throws at import time without env vars.
 * Pages reach the network only through `lib/client`, which each suite mocks.
 */
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router'
import { act, render, type RenderResult } from '@testing-library/react'
import { vi } from 'vitest'
import { AuthContext, type AuthContextValue } from '../auth/context'
import type { CheckIn, Profile, Trends } from '../lib/api'

/**
 * The context value a test hands to a screen.
 *
 * `Omit<..., 'profileLoading'> & { profileLoading: boolean }` rather than an `extends`, so
 * this type is correct BOTH before the implementation adds `profileLoading` to
 * `AuthContextValue` (Omit of an absent key is a no-op, and we add the field the contract
 * says will exist) and after it does (Omit removes it, we re-add the identical type). Either
 * way `FakeAuth` is assignable to `AuthContextValue`, so nobody has to edit the oracle to
 * make the build pass. No `any` anywhere.
 */
export type FakeAuth = Omit<AuthContextValue, 'profileLoading'> & { profileLoading: boolean }

export function aProfile(overrides: Partial<Profile> = {}): Profile {
  return {
    id: 'profile-1',
    display_name: 'Test Athlete',
    weight_unit: 'lb',
    goal: 'squat 315',
    // Onboarded (goal + consent) so `resolveDestination` never redirects out from under a
    // screen we are trying to assert on.
    consented_at: '2026-01-01T00:00:00Z',
    timezone: 'UTC',
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

/** Raw text of the fixture check-in. Asserted as DATA, never as UI copy. */
export const CHECK_IN_TEXT = 'fixture-row-bench-135-4x8'

export function aCheckIn(overrides: Partial<CheckIn> = {}): CheckIn {
  return {
    id: 'check-in-1',
    raw_text: CHECK_IN_TEXT,
    source: 'text',
    entry_date: '2026-08-23',
    created_at: '2026-08-23T12:00:00Z',
    // 'done' with no facts: `Facts.tsx` renders a `role="alert"` for 'failed'/'partial', and
    // a reply on /app renders a `role="status"` (#41). Neither belongs in a fixture whose
    // job is to prove the CONTENT state carries no placeholder and no state-level error.
    extraction_status: 'done',
    facts: { sets: [], nutrition: [], sleep: [], bodyweight: [] },
    reply: null,
    ...overrides,
  }
}

/** Name of the movement in the non-empty trends fixture. Asserted as DATA, not as copy. */
export const TRENDS_EXERCISE = 'fixture-back-squat'

export function someTrends(): Trends {
  return {
    start_date: '2026-07-25',
    end_date: '2026-08-23',
    volume: [{ date: '2026-08-23', volume_kg: '1000.00', bodyweight_sets: 0, bodyweight_reps: 0 }],
    exercises: [
      { name: TRENDS_EXERCISE, sets: 5, reps: 25, volume_kg: '1000.00', heaviest_kg: '100.00' },
    ],
    sleep: [{ date: '2026-08-23', hours: '7.50', quality: 4 }],
    bodyweight: [{ date: '2026-08-23', weight_kg: '80.00' }],
    nutrition: [
      { date: '2026-08-23', calories: '2400', protein_g: '180', carbs_g: '250', fat_g: '80' },
    ],
  }
}

/** A window the server answered for, with nothing in it — the EMPTY state, not a failure. */
export function noTrends(): Trends {
  return {
    start_date: '2026-07-25',
    end_date: '2026-08-23',
    volume: [],
    exercises: [],
    sleep: [],
    bodyweight: [],
    nutrition: [],
  }
}

export function fakeAuth(overrides: Partial<FakeAuth> = {}): FakeAuth {
  return {
    status: 'signedIn',
    session: null,
    profile: aProfile(),
    profileError: false,
    profileLoading: false,
    refreshProfile: vi.fn(() => Promise.resolve()),
    signOut: vi.fn(() => Promise.resolve()),
    ...overrides,
  }
}

/**
 * Mount anything that reads auth, inside a router (AppShell renders `NavLink`).
 *
 * The context value is built ONCE, outside the render, and never rebuilt: every screen's
 * fetch effect depends transitively on `signOut`, so a value rebuilt per render would hand
 * them a new function identity each time and spin the effect forever. That would be a
 * harness bug masquerading as a product bug.
 */
export function renderWithAuth(
  ui: ReactElement,
  auth: Partial<FakeAuth> = {},
  initialEntries: string[] = ['/app'],
): RenderResult {
  const value = fakeAuth(auth)
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <AuthContext.Provider value={value}>{ui}</AuthContext.Provider>
    </MemoryRouter>,
  )
}

// ---------------------------------------------------------------------------
// The category rules.
// ---------------------------------------------------------------------------

/** Every element carrying `data-state`, in document order. */
export function stateElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>('[data-state]'))
}

/**
 * THE state element. Exactly one branch may be on screen at a time (#43 row 19), so anything
 * other than one is a failure here rather than a silently-picked first match.
 */
export function stateEl(container: HTMLElement): HTMLElement {
  const found = stateElements(container)
  if (found.length !== 1) {
    const kinds = found.map((el) => el.getAttribute('data-state')).join(', ')
    throw new Error(
      `expected exactly one [data-state] element on screen, found ${found.length}` +
        (found.length > 0 ? ` (${kinds})` : ''),
    )
  }
  return found[0]
}

export function stateKind(container: HTMLElement): string | null {
  return stateEl(container).getAttribute('data-state')
}

/**
 * The shared loading placeholder. The marker is the contract: one component emits
 * `[data-component="skeleton"]`, so three screens matching it cannot be three lookalikes.
 */
export function requireSkeleton(root: ParentNode): HTMLElement {
  const found = root.querySelector<HTMLElement>('[data-component="skeleton"]')
  if (found === null) {
    throw new Error('expected the shared skeleton ([data-component="skeleton"]) inside this state')
  }
  return found
}

export function findSkeleton(root: ParentNode): HTMLElement | null {
  return root.querySelector<HTMLElement>('[data-component="skeleton"]')
}

/**
 * Is there anything the user can actually SEE here?
 *
 * The bug this feature exists to kill is a state that renders as blank, so "not blank" has to
 * be a rule rather than a copy match: real text, or a marked placeholder, or a live region.
 * An empty container and a `return null` both fail it — which is exactly what they should do.
 */
export function hasVisibleTreatment(container: HTMLElement): boolean {
  if ((container.textContent ?? '').trim() !== '') return true
  return container.querySelector('[data-component="skeleton"], [role="status"]') !== null
}

/**
 * The ways out of a screen: an enabled control, or a link that goes somewhere.
 *
 * Used by #46 row 10, which is about the CATEGORY "terminal-looking state" rather than about
 * any particular button label — a state with no member of this set is a dead end regardless
 * of what it says.
 */
export function wayOutControls(container: HTMLElement): HTMLElement[] {
  const buttons = Array.from(
    container.querySelectorAll<HTMLElement>('button, [role="button"]'),
  ).filter(
    (el) =>
      !(el instanceof HTMLButtonElement && el.disabled) &&
      el.getAttribute('aria-disabled') !== 'true',
  )
  const links = Array.from(container.querySelectorAll<HTMLAnchorElement>('a[href]')).filter(
    (el) => (el.getAttribute('href') ?? '') !== '',
  )
  return [...buttons, ...links]
}

/** A promise that never settles — the in-flight state, held open for as long as we look. */
export function pending<T>(): Promise<T> {
  return new Promise<T>(() => {})
}

/** Let the mocked API promise land and React re-render, without an act() warning. */
export async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}
