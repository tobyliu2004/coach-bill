/**
 * Oracle suite for issue #19, section D — frontend & error states (rows 17-20).
 *
 * Part of commit #1 on `feat/19-ai-extraction`, written BEFORE any implementation exists.
 * These encode section D of the 23-row correctness table Toby approved on 2026-07-16 —
 * the dimension the #18 retro found missing, where every reviewer catch was a frontend
 * error state that no table row covered.
 *
 * `./checkInView` does not exist yet: this file failing to resolve it is the CORRECT
 * initial failure.
 *
 * Why a new module rather than testing AppHome.tsx: vitest here is `src/**` + node env
 * with no DOM emulation (vite.config.ts), so a `.tsx` component is untestable today.
 * Rows 17-20 are all pure DECISIONS — which block to render, what an error means — so the
 * build extracts those decisions into this module and AppHome renders what it returns.
 * That keeps the oracle on the logic instead of on markup.
 */
import { describe, expect, it } from 'vitest'
import { ApiAuthError, ApiError, type CheckIn } from './api'
import { errorAction, factsView, listView } from './checkInView'

const NO_FACTS: CheckIn['facts'] = { sets: [], nutrition: [], sleep: [], bodyweight: [] }

function checkIn(overrides: Partial<CheckIn> = {}): CheckIn {
  return {
    id: '9a3b1c2d-0000-4000-8000-000000000000',
    raw_text: 'bench 135 4x8',
    source: 'text',
    entry_date: '2026-07-16',
    created_at: '2026-07-16T12:00:00Z',
    extraction_status: 'done',
    facts: NO_FACTS,
    ...overrides,
  }
}

describe('factsView', () => {
  // AC row 17: extraction_status 'failed' -> the UI says extraction FAILED, and that is
  // visibly distinct from "no facts found". This is #18's exact bug: a failure rendering
  // as an empty state looks like data loss.
  it("reports 'failed' for a check-in whose extraction failed", () => {
    expect(factsView(checkIn({ extraction_status: 'failed', facts: NO_FACTS }))).toEqual({
      kind: 'failed',
    })
  })

  // AC row 18: status 'done' with zero facts -> no facts block and NO error. "Nothing to
  // extract" is success (Toby's row-11 decision), so it must not look broken.
  it("reports 'none' for a done check-in with zero facts", () => {
    expect(factsView(checkIn({ extraction_status: 'done', facts: NO_FACTS }))).toEqual({
      kind: 'none',
    })
  })

  // AC rows 17+18 together: the two states must be DISTINGUISHABLE. Asserting each shape
  // separately would still pass an implementation that mapped both onto one value, which
  // is precisely the bug — so assert the distinction itself.
  it('distinguishes a failed extraction from an empty one', () => {
    const failed = factsView(checkIn({ extraction_status: 'failed', facts: NO_FACTS }))
    const empty = factsView(checkIn({ extraction_status: 'done', facts: NO_FACTS }))

    expect(failed.kind).not.toBe(empty.kind)
  })

  // AC row 18 (the other side of "no facts block"): when there ARE facts, they render.
  // Without this, `factsView` returning 'none' unconditionally would satisfy row 18.
  it("reports 'facts' when the check-in actually has extracted facts", () => {
    const view = factsView(
      checkIn({
        extraction_status: 'done',
        facts: {
          ...NO_FACTS,
          sets: [
            {
              id: '1e0acb28-0000-4000-8000-000000000000',
              exercise_name: 'bench press',
              set_number: 1,
              reps: 8,
              weight_kg: '61.235',
            },
          ],
        },
      }),
    )

    expect(view.kind).toBe('facts')
  })
})

describe('errorAction', () => {
  // AC row 19: the session expires mid-POST -> ApiAuthError means SIGN OUT, not an
  // in-app message and not a stuck spinner. The #18 retro caught this exact miss.
  it('maps ApiAuthError to sign-out', () => {
    expect(errorAction(new ApiAuthError('session rejected by the API'))).toEqual({
      kind: 'sign-out',
    })
  })

  // AC row 19 (the boundary): every OTHER failure is a transient in-app message — signing
  // the user out on a 500 would be its own bug.
  it('maps a non-auth ApiError to an in-app message', () => {
    expect(errorAction(new ApiError(500, '/check-ins failed'))).toEqual({ kind: 'message' })
  })

  it('maps an unknown thrown value to an in-app message', () => {
    expect(errorAction(new TypeError('network down'))).toEqual({ kind: 'message' })
  })
})

describe('listView', () => {
  // AC row 20: the list fetch fails entirely -> the load-FAILED state, never "no check-ins
  // yet". A failure that renders as the empty state looks like data loss — this is the
  // regression guard on #18's fix, and the two inputs it must separate are identical
  // except for `loadFailed`.
  it("reports 'load-failed' when the fetch failed, not the empty state", () => {
    expect(listView({ loading: false, loadFailed: true, checkIns: [] })).toEqual({
      kind: 'load-failed',
    })
  })

  it("reports 'empty' only when the fetch SUCCEEDED and returned nothing", () => {
    expect(listView({ loading: false, loadFailed: false, checkIns: [] })).toEqual({ kind: 'empty' })
  })

  it('renders the list when the fetch returned check-ins', () => {
    const row = checkIn()

    expect(listView({ loading: false, loadFailed: false, checkIns: [row] })).toEqual({
      kind: 'list',
      checkIns: [row],
    })
  })

  it('reports loading before the first fetch settles', () => {
    expect(listView({ loading: true, loadFailed: false, checkIns: [] })).toEqual({ kind: 'loading' })
  })
})
