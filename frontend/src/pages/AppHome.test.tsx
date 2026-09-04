/**
 * Oracle suite for issue #43 — /app (rows 1-6).
 *
 * Written before the implementation exists. Every test names the acceptance-criteria row it
 * grades. The assertions are on the STATE CATEGORY the contract defines — the single
 * `data-state` element, the shared `[data-component="skeleton"]`, ARIA roles — and never on
 * copy: a test that greps for a sentence breaks when the sentence is reworded and passes when
 * a different screen happens to say it.
 *
 * SCOPING, and it is load-bearing: /app has TWO possible `role="alert"` nodes — the
 * compose/delete error and the load-failed one. Every "an error is / is not shown" assertion
 * here is scoped INSIDE the relevant `[data-state]` element, plus a check that the
 * load-failed branch is absent from the document. A bare global `queryByRole('alert')` would
 * be an assertion that cannot fail for the reason it claims to.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, within } from '@testing-library/react'
import { ApiError, type CheckIn } from '../lib/api'
import {
  CHECK_IN_TEXT,
  aCheckIn,
  findSkeleton,
  flush,
  pending,
  renderWithAuth,
  requireSkeleton,
  stateEl,
} from '../test/harness'

// Mocked so the page never reaches the network, and — the reason this shape is mandatory —
// so `lib/supabase`'s import-time env throw never enters the module graph.
vi.mock('../lib/client', () => ({
  api: {
    getMe: vi.fn(),
    updateMe: vi.fn(),
    createCheckIn: vi.fn(),
    listCheckIns: vi.fn(),
    getTrends: vi.fn(),
    requestReply: vi.fn(),
    deleteCheckIn: vi.fn(),
  },
}))

import { api } from '../lib/client'
import AppHome from './AppHome'

const listCheckIns = vi.mocked(api.listCheckIns)

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('/app state rendering (#43 rows 1-6)', () => {
  it('row 1: list fetch in flight → the loading placeholder is on screen', () => {
    listCheckIns.mockReturnValue(pending<CheckIn[]>())

    const { container } = renderWithAuth(<AppHome />)

    const el = stateEl(container)
    expect(el.getAttribute('data-state')).toBe('loading')
    const skeleton = requireSkeleton(el)
    expect(skeleton.getAttribute('role')).toBe('status')
    // ...and it is announced with a real name, not an anonymous grey box.
    expect(within(el).getAllByRole('status', { name: /\S/ })).toContain(skeleton)
  })

  it('row 2: in flight → the empty state is NOT on screen', () => {
    listCheckIns.mockReturnValue(pending<CheckIn[]>())

    const { container } = renderWithAuth(<AppHome />)

    expect(stateEl(container).getAttribute('data-state')).toBe('loading')
    expect(container.querySelector('[data-state="empty"]')).toBeNull()
  })

  it('row 3: in flight → no error alert is on screen', () => {
    listCheckIns.mockReturnValue(pending<CheckIn[]>())

    const { container } = renderWithAuth(<AppHome />)

    const el = stateEl(container)
    expect(el.getAttribute('data-state')).toBe('loading')
    // Scoped to the state element: the compose/delete alert lives outside it and must not be
    // able to satisfy — or break — this assertion.
    expect(within(el).queryByRole('alert')).toBeNull()
    // ...and the failure branch is not rendered somewhere else on the page either.
    expect(container.querySelector('[data-state="load-failed"]')).toBeNull()
  })

  it('row 4: fetch failed → the error alert is on screen and the placeholder is gone', async () => {
    listCheckIns.mockRejectedValue(new ApiError(500, 'boom'))

    const { container } = renderWithAuth(<AppHome />)
    await flush()

    const el = stateEl(container)
    expect(el.getAttribute('data-state')).toBe('load-failed')
    expect(within(el).getAllByRole('alert').length).toBeGreaterThan(0)
    expect(findSkeleton(container)).toBeNull()
  })

  it('row 5: fetch OK, zero check-ins → the empty state, no placeholder, no alert', async () => {
    listCheckIns.mockResolvedValue([])

    const { container } = renderWithAuth(<AppHome />)
    await flush()

    const el = stateEl(container)
    expect(el.getAttribute('data-state')).toBe('empty')
    expect(findSkeleton(container)).toBeNull()
    expect(within(el).queryByRole('status')).toBeNull()
    expect(within(el).queryByRole('alert')).toBeNull()
    expect(container.querySelector('[data-state="load-failed"]')).toBeNull()
  })

  it('row 6: fetch OK, one check-in → the rows render, no placeholder', async () => {
    listCheckIns.mockResolvedValue([aCheckIn()])

    const { container } = renderWithAuth(<AppHome />)
    await flush()

    const el = stateEl(container)
    expect(el.getAttribute('data-state')).toBe('content')
    // The row is really there — asserted on the fixture's DATA, not on any UI wording.
    expect(within(el).getByText(CHECK_IN_TEXT)).toBeTruthy()
    expect(findSkeleton(container)).toBeNull()
    // TABLE AMENDMENT — DIRECTION: WIDENING (this test can newly PASS in a case the original
    // clause would have failed). Approved by Toby before the oracle commit; recorded on #43.
    //
    // The contract first said the `content` state carries no `role="status"`. That is FALSE
    // about correct code on THIS screen: a delivered coach reply renders
    // `role="status" aria-live="polite"` on /app deliberately (#41 — the user submitted and
    // is waiting for exactly that reply). The clause also pinned nothing the line above does
    // not already pin, because a loading treatment left behind IS a skeleton and the skeleton
    // marker is what identifies it. So it is removed here rather than propped up by a
    // `reply: null` fixture that merely hid the conflict.
    //
    // It is KEPT on /history and /trends, where it is true and load-bearing: History mounts
    // its cards with `live={false}`, and the Trends dashboard has no live region at all.
  })
})
