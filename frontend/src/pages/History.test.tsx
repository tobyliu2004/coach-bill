/**
 * Oracle suite for issue #43 — /history (rows 7-12, i.e. rows 1-6 for this screen).
 *
 * Written before the implementation exists. Assertions are on the state CATEGORY the
 * contract defines (`data-state`, the shared `[data-component="skeleton"]`, ARIA roles),
 * never on copy. Error assertions are scoped inside the state element: `Facts.tsx` can emit
 * its own `role="alert"` inside a check-in card, so a global alert query would be measuring
 * something else.
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
import History from './History'

const listCheckIns = vi.mocked(api.listCheckIns)

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('/history state rendering (#43 rows 7-12)', () => {
  it('row 7 (=row 1 for /history): fetch in flight → the loading placeholder is on screen', () => {
    listCheckIns.mockReturnValue(pending<CheckIn[]>())

    const { container } = renderWithAuth(<History />, {}, ['/history'])

    const el = stateEl(container)
    expect(el.getAttribute('data-state')).toBe('loading')
    const skeleton = requireSkeleton(el)
    expect(skeleton.getAttribute('role')).toBe('status')
    expect(within(el).getAllByRole('status', { name: /\S/ })).toContain(skeleton)
  })

  it('row 8 (=row 2): in flight → the empty state is NOT on screen', () => {
    listCheckIns.mockReturnValue(pending<CheckIn[]>())

    const { container } = renderWithAuth(<History />, {}, ['/history'])

    expect(stateEl(container).getAttribute('data-state')).toBe('loading')
    expect(container.querySelector('[data-state="empty"]')).toBeNull()
  })

  it('row 9 (=row 3): in flight → no error alert is on screen', () => {
    listCheckIns.mockReturnValue(pending<CheckIn[]>())

    const { container } = renderWithAuth(<History />, {}, ['/history'])

    const el = stateEl(container)
    expect(el.getAttribute('data-state')).toBe('loading')
    expect(within(el).queryByRole('alert')).toBeNull()
    expect(container.querySelector('[data-state="load-failed"]')).toBeNull()
  })

  it('row 10 (=row 4): fetch failed → the alert is on screen and the placeholder is gone', async () => {
    listCheckIns.mockRejectedValue(new ApiError(500, 'boom'))

    const { container } = renderWithAuth(<History />, {}, ['/history'])
    await flush()

    const el = stateEl(container)
    expect(el.getAttribute('data-state')).toBe('load-failed')
    expect(within(el).getAllByRole('alert').length).toBeGreaterThan(0)
    expect(findSkeleton(container)).toBeNull()
  })

  it('row 11 (=row 5): fetch OK, zero check-ins → the empty state, no placeholder, no alert', async () => {
    listCheckIns.mockResolvedValue([])

    const { container } = renderWithAuth(<History />, {}, ['/history'])
    await flush()

    const el = stateEl(container)
    expect(el.getAttribute('data-state')).toBe('empty')
    expect(findSkeleton(container)).toBeNull()
    expect(within(el).queryByRole('status')).toBeNull()
    expect(within(el).queryByRole('alert')).toBeNull()
    expect(container.querySelector('[data-state="load-failed"]')).toBeNull()
  })

  it('row 12 (=row 6): fetch OK, one check-in → the rows render, no placeholder', async () => {
    listCheckIns.mockResolvedValue([aCheckIn()])

    const { container } = renderWithAuth(<History />, {}, ['/history'])
    await flush()

    const el = stateEl(container)
    expect(el.getAttribute('data-state')).toBe('content')
    expect(within(el).getByText(CHECK_IN_TEXT)).toBeTruthy()
    expect(findSkeleton(container)).toBeNull()
    // History mounts its cards with `live={false}`, so nothing in a healthy content branch
    // may announce: any `role="status"` here would be a placeholder left behind.
    expect(within(el).queryByRole('status')).toBeNull()
  })
})
