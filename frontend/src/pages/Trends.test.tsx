/**
 * Oracle suite for issue #43 — /trends (rows 13-18, i.e. rows 1-6 for this screen).
 *
 * Written before the implementation exists. Same category rules as the other two screens:
 * that is the point of #43 — one shared treatment across three screens, because three copies
 * of a fix is how they drift.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, within } from '@testing-library/react'
import { ApiError, type Trends as TrendsPayload } from '../lib/api'
import {
  TRENDS_EXERCISE,
  findSkeleton,
  flush,
  noTrends,
  pending,
  renderWithAuth,
  requireSkeleton,
  someTrends,
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
import Trends from './Trends'

const getTrends = vi.mocked(api.getTrends)

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('/trends state rendering (#43 rows 13-18)', () => {
  it('row 13 (=row 1 for /trends): fetch in flight → the loading placeholder is on screen', () => {
    getTrends.mockReturnValue(pending<TrendsPayload>())

    const { container } = renderWithAuth(<Trends />, {}, ['/trends'])

    const el = stateEl(container)
    expect(el.getAttribute('data-state')).toBe('loading')
    const skeleton = requireSkeleton(el)
    expect(skeleton.getAttribute('role')).toBe('status')
    expect(within(el).getAllByRole('status', { name: /\S/ })).toContain(skeleton)
  })

  it('row 14 (=row 2): in flight → the empty state is NOT on screen', () => {
    getTrends.mockReturnValue(pending<TrendsPayload>())

    const { container } = renderWithAuth(<Trends />, {}, ['/trends'])

    expect(stateEl(container).getAttribute('data-state')).toBe('loading')
    expect(container.querySelector('[data-state="empty"]')).toBeNull()
  })

  it('row 15 (=row 3): in flight → no error alert is on screen', () => {
    getTrends.mockReturnValue(pending<TrendsPayload>())

    const { container } = renderWithAuth(<Trends />, {}, ['/trends'])

    const el = stateEl(container)
    expect(el.getAttribute('data-state')).toBe('loading')
    expect(within(el).queryByRole('alert')).toBeNull()
    expect(container.querySelector('[data-state="load-failed"]')).toBeNull()
  })

  it('row 16 (=row 4): fetch failed → the alert is on screen and the placeholder is gone', async () => {
    getTrends.mockRejectedValue(new ApiError(500, 'boom'))

    const { container } = renderWithAuth(<Trends />, {}, ['/trends'])
    await flush()

    const el = stateEl(container)
    expect(el.getAttribute('data-state')).toBe('load-failed')
    expect(within(el).getAllByRole('alert').length).toBeGreaterThan(0)
    expect(findSkeleton(container)).toBeNull()
  })

  it('row 17 (=row 5): fetch OK, an empty window → the empty state, no placeholder, no alert', async () => {
    getTrends.mockResolvedValue(noTrends())

    const { container } = renderWithAuth(<Trends />, {}, ['/trends'])
    await flush()

    const el = stateEl(container)
    expect(el.getAttribute('data-state')).toBe('empty')
    expect(findSkeleton(container)).toBeNull()
    expect(within(el).queryByRole('status')).toBeNull()
    expect(within(el).queryByRole('alert')).toBeNull()
    expect(container.querySelector('[data-state="load-failed"]')).toBeNull()
  })

  it('row 18 (=row 6): fetch OK, data in the window → the dashboard renders, no placeholder', async () => {
    getTrends.mockResolvedValue(someTrends())

    const { container } = renderWithAuth(<Trends />, {}, ['/trends'])
    await flush()

    const el = stateEl(container)
    expect(el.getAttribute('data-state')).toBe('content')
    // Real payload data on screen — not a wording match.
    expect(within(el).getByText(TRENDS_EXERCISE)).toBeTruthy()
    expect(findSkeleton(container)).toBeNull()
    expect(within(el).queryByRole('status')).toBeNull()
  })
})
