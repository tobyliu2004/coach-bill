/**
 * Oracle suite for issue #43 rows 19-20 — the two rows that are about the three screens
 * TOGETHER rather than about any one of them.
 *
 * Row 19: exactly one state renders, never two at once.
 * Row 20: the loading placeholder is the same shared component on all three, not three
 *         lookalikes that will drift apart the first time one of them is touched.
 *
 * Both are written over a table of the three screens crossed with the four states — the
 * closed set IS the thing that drifts, which is why it is enumerated here and nowhere else.
 * The assertions themselves are category rules (`data-state`, `[data-component="skeleton"]`),
 * not copy.
 */
import type { ReactElement } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, within } from '@testing-library/react'
import { ApiError, type CheckIn, type Trends as TrendsPayload } from '../lib/api'
import {
  aCheckIn,
  flush,
  noTrends,
  pending,
  renderWithAuth,
  requireSkeleton,
  someTrends,
  stateElements,
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
import AppHome from './AppHome'
import History from './History'
import Trends from './Trends'

const listCheckIns = vi.mocked(api.listCheckIns)
const getTrends = vi.mocked(api.getTrends)

type StateKind = 'loading' | 'load-failed' | 'empty' | 'content'

interface ScreenSpec {
  readonly path: string
  readonly element: ReactElement
  /** Point this screen's one network call at the outcome that produces `kind`. */
  readonly arrange: (kind: StateKind) => void
}

const LIST_SCREEN_ARRANGE = (kind: StateKind): void => {
  if (kind === 'loading') listCheckIns.mockReturnValue(pending<CheckIn[]>())
  else if (kind === 'load-failed') listCheckIns.mockRejectedValue(new ApiError(500, 'boom'))
  else if (kind === 'empty') listCheckIns.mockResolvedValue([])
  else listCheckIns.mockResolvedValue([aCheckIn()])
}

const SCREENS: readonly ScreenSpec[] = [
  { path: '/app', element: <AppHome />, arrange: LIST_SCREEN_ARRANGE },
  { path: '/history', element: <History />, arrange: LIST_SCREEN_ARRANGE },
  {
    path: '/trends',
    element: <Trends />,
    arrange: (kind) => {
      if (kind === 'loading') getTrends.mockReturnValue(pending<TrendsPayload>())
      else if (kind === 'load-failed') getTrends.mockRejectedValue(new ApiError(500, 'boom'))
      else if (kind === 'empty') getTrends.mockResolvedValue(noTrends())
      else getTrends.mockResolvedValue(someTrends())
    },
  },
]

const STATES: readonly StateKind[] = ['loading', 'load-failed', 'empty', 'content']

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('#43 row 19: exactly one state renders, never two at once', () => {
  for (const screen of SCREENS) {
    for (const kind of STATES) {
      it(`row 19: ${screen.path} in the ${kind} state renders exactly one [data-state]`, async () => {
        screen.arrange(kind)

        const { container } = renderWithAuth(screen.element, {}, [screen.path])
        await flush()

        const found = stateElements(container)
        expect(
          found.map((el) => el.getAttribute('data-state')),
          `${screen.path} should render exactly one state element`,
        ).toEqual([kind])
      })
    }
  }
})

describe('#43 row 20: one shared loading placeholder, not three lookalikes', () => {
  it('row 20: all three screens render the same [data-component="skeleton"] while loading', () => {
    const seen: string[] = []

    for (const screen of SCREENS) {
      screen.arrange('loading')

      const { container } = renderWithAuth(screen.element, {}, [screen.path])

      const el = stateElements(container)[0]
      expect(el, `${screen.path} renders no [data-state] element while loading`).toBeTruthy()
      // The marker only `components/Skeleton.tsx` emits. Three screens matching it are, by
      // construction, one component — which is what this row is for.
      const skeleton = requireSkeleton(el)
      expect(skeleton.getAttribute('role'), `${screen.path} skeleton role`).toBe('status')
      expect(within(el).getAllByRole('status', { name: /\S/ })).toContain(skeleton)
      seen.push(screen.path)

      cleanup()
    }

    expect(seen).toEqual(SCREENS.map((screen) => screen.path))
  })
})
