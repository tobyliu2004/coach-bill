/**
 * Turning stored facts into the strings a person reads.
 *
 * Separate from checkInView.ts (which decides WHICH state to show) and from AppHome (which
 * decides what it LOOKS like). This module answers "what does this number say?", and it is
 * pure, so it is tested — the numbers are the whole product.
 *
 * Weights come back from the API in canonical kg as strings (Postgres `numeric` -> JSON
 * string; see api.ts). We show them back in the user's OWN unit: they typed "bench 135" in
 * pounds, so the check-in must read 135 lb, not 61.235 kg. This is exactly why the backend
 * rounds lb->kg to three decimals — 61.235 kg converts back to 135.0 lb precisely, so the
 * round trip is lossless at any precision a person cares about.
 */
import type { CheckIn, WorkoutSet } from './api'

const LB_PER_KG = 2.2046226218487757 // 1 / 0.45359237, the exact avoirdupois pound

/**
 * A weight for display, in the user's unit, with the unit appended. `null` in -> `null` out:
 * a bodyweight move has no load, and rendering "0 lb" would claim a fact we don't have.
 *
 * Parsing the string to a float here is deliberate and safe: this is the one place the value
 * stops being data and becomes text. Gym weights are 3-4 significant digits; float error
 * shows up ~15 digits in, and we round to 1 decimal anyway.
 */
export function formatWeight(weightKg: string | null, unit: 'lb' | 'kg'): string | null {
  if (weightKg === null) return null
  const kg = Number(weightKg)
  if (!Number.isFinite(kg)) return null
  const value = unit === 'kg' ? kg : kg * LB_PER_KG
  // Trim a trailing ".0": people write 135, not 135.0. Anything genuinely fractional keeps
  // its decimal.
  return `${Number(value.toFixed(1))} ${unit}`
}

export interface SetLine {
  key: string
  exercise: string
  /** e.g. "4×8" — sets×reps, the way lifters write it. */
  volume: string
  /** e.g. "135 lb", or null for a bodyweight move. */
  load: string | null
}

/**
 * Collapse per-set rows into the line a lifter would actually write.
 *
 * The database stores one row per set (that's the schema's shape and what Trends needs), but
 * "bench press 8 @ 135 / bench press 8 @ 135 / bench press 8 @ 135 / bench press 8 @ 135" is
 * not how anyone reads their own log. Consecutive sets of the same exercise at the same
 * reps and load collapse to "bench press 4×8 135 lb". A change in either reps or load starts
 * a new line, so a real pyramid (5@135, 3@185) still reads as two lines and nothing is lost.
 */
export function toSetLines(sets: WorkoutSet[], unit: 'lb' | 'kg'): SetLine[] {
  const lines: SetLine[] = []
  for (const set of sets) {
    const load = formatWeight(set.weight_kg, unit)
    const previous = lines[lines.length - 1]
    const sameAsPrevious =
      previous !== undefined &&
      previous.exercise === set.exercise_name &&
      previous.load === load &&
      previous.volume.endsWith(`×${set.reps}`)

    if (sameAsPrevious) {
      const count = Number(previous.volume.split('×')[0]) + 1
      previous.volume = `${count}×${set.reps}`
    } else {
      lines.push({ key: set.id, exercise: set.exercise_name, volume: `1×${set.reps}`, load })
    }
  }
  return lines
}

/** "6h" / "6h · 4/5" — quality only when the user actually rated it. */
export function formatSleep(hours: string, quality: number | null): string {
  const value = Number(hours)
  const shown = Number.isFinite(value) ? Number(value.toFixed(1)) : hours
  return quality === null ? `${shown}h` : `${shown}h · ${quality}/5`
}

/** "310 cal · 25P / 2C / 22F" — the macros, compact. */
export function formatMacros(entry: CheckIn['facts']['nutrition'][number]): string {
  const round = (n: string): number | string => {
    const value = Number(n)
    return Number.isFinite(value) ? Math.round(value) : n
  }
  return `${round(entry.calories)} cal · ${round(entry.protein_g)}P / ${round(entry.carbs_g)}C / ${round(entry.fat_g)}F`
}
