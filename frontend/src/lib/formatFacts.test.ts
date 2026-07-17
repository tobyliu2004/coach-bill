/**
 * Display formatting for extracted facts.
 *
 * NOT part of issue #19's oracle — the approved correctness table says nothing about how a
 * weight is rendered, and I'm not going to smuggle rows into it. These are ordinary unit
 * tests for a pure module, written alongside the code they cover. The load-bearing one is
 * the lb round trip: 135 lb is stored as 61.235 kg, and the whole reason the backend rounds
 * to three decimals is that it has to come back as 135 — if it renders "134.9", the number
 * the user typed is not the number the app shows them.
 */
import { describe, expect, it } from 'vitest'
import { formatMacros, formatSleep, formatWeight, toSetLines } from './formatFacts'
import type { WorkoutSet } from './api'

function set(overrides: Partial<WorkoutSet> = {}): WorkoutSet {
  return {
    id: crypto.randomUUID(),
    exercise_name: 'bench press',
    set_number: 1,
    reps: 8,
    weight_kg: '61.235',
    ...overrides,
  }
}

describe('formatWeight', () => {
  it('round-trips a stored kg weight back to the pounds the user typed', () => {
    expect(formatWeight('61.235', 'lb')).toBe('135 lb')
    expect(formatWeight('81.647', 'lb')).toBe('180 lb')
  })

  it('shows kg users their kg unconverted', () => {
    expect(formatWeight('135', 'kg')).toBe('135 kg')
  })

  it('returns null for a bodyweight move rather than claiming 0', () => {
    expect(formatWeight(null, 'lb')).toBeNull()
    expect(formatWeight(null, 'kg')).toBeNull()
  })

  it('keeps a genuinely fractional weight', () => {
    expect(formatWeight('2.5', 'kg')).toBe('2.5 kg')
  })
})

describe('toSetLines', () => {
  it('collapses four identical sets into one 4×8 line', () => {
    const sets = [1, 2, 3, 4].map((n) => set({ set_number: n }))

    expect(toSetLines(sets, 'lb')).toEqual([
      { key: sets[0].id, exercise: 'bench press', volume: '4×8', load: '135 lb' },
    ])
  })

  it('starts a new line when the load changes, so a pyramid still reads correctly', () => {
    const lines = toSetLines(
      [
        set({ set_number: 1, reps: 5, weight_kg: '61.235' }),
        set({ set_number: 2, reps: 3, weight_kg: '83.915' }),
      ],
      'lb',
    )

    expect(lines.map((l) => `${l.volume} ${l.load}`)).toEqual(['1×5 135 lb', '1×3 185 lb'])
  })

  it('does not merge different exercises', () => {
    const lines = toSetLines([set({ exercise_name: 'squat' }), set({ exercise_name: 'deadlift' })], 'lb')

    expect(lines).toHaveLength(2)
  })

  it('renders a bodyweight move with no load', () => {
    const lines = toSetLines([set({ exercise_name: 'pushups', reps: 20, weight_kg: null })], 'lb')

    expect(lines).toEqual([
      { key: lines[0].key, exercise: 'pushups', volume: '1×20', load: null },
    ])
  })
})

describe('formatSleep', () => {
  it('omits quality when the user did not rate it', () => {
    expect(formatSleep('6', null)).toBe('6h')
  })

  it('shows quality when there is one', () => {
    expect(formatSleep('6.5', 4)).toBe('6.5h · 4/5')
  })
})

describe('formatMacros', () => {
  it('renders calories and all four macros, rounded', () => {
    expect(
      formatMacros({
        id: 'a',
        description: '4 eggs',
        calories: '310.4',
        protein_g: '25',
        carbs_g: '2',
        fat_g: '22',
        meal: null,
      }),
    ).toBe('310 cal · 25P / 2C / 22F')
  })
})
