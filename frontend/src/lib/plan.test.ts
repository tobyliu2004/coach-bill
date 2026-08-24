/**
 * Oracle suite for issue #51, section C + section D's frontend rows — the plan and diet
 * screens' DECISIONS.
 *
 * Part of commit #1 on `feat/plan-and-diet`, written BEFORE any implementation exists.
 * Rows covered here, from the 26-row correctness table approved on issue #51:
 *
 *   row 18 — plan targets 2400 kcal, 1800 logged -> "1800 / 2400", two numbers, never a %
 *   row 19 — no active plan -> logged intake with NO target line, never a target of 0
 *   row 21 — plan exists, nothing logged -> "0 / 2400" WITH the target line
 *   row 22 — /plan while fetching -> a loading state visibly distinct from empty
 *   row 23 — /plan fetch fails -> an error WITH a retry, never a silent "you have no plan"
 *   row 24 — POST /plans in flight -> "Bill is writing your plan…", distinct from loading
 *            and from empty, and the button cannot be double-fired
 *
 * `./plan` does not exist yet, and `Plan`/`createPlan`/`getCurrentPlan` are not in `./api`
 * yet: this file failing to resolve them is the CORRECT initial failure.
 *
 * WHY THIS IS A `lib/` MODULE AND NOT A COMPONENT TEST. vitest here is node env over
 * `src/**` with no DOM (vite.config.ts), so a `.tsx` cannot be mounted — the #18/#39/#40
 * lesson, three times paid for. So the question "WHICH state is this?" lives in `lib/plan.ts`
 * and is asserted here; "what does that state look like?" lives in the pages and is reviewed
 * by eye. Every bug those retros shipped was in the first question.
 *
 * THE SHAPE THIS SUITE PINS (the implementation is free in everything else):
 *   - `PlanView` is a discriminated union on `kind` with FIVE distinct members —
 *     'loading' | 'load-failed' | 'generating' | 'empty' | 'plan' — and EVERY member
 *     carries `canGenerate: boolean`, because the page renders one submit button in every
 *     state and row 24 is about that button.
 *   - `DietView` is a discriminated union on `kind`: 'no-target' (row 19) has no `target`
 *     key at all, 'target' (rows 18, 21) has one. Numbers are `number | null`, already
 *     parsed — the JSX does no arithmetic and no `Number(...)`.
 */
import { describe, expect, it, vi } from 'vitest'
import { createApi, type Plan } from './api'
import { dietRequest, dietView, planRequest, planView } from './plan'

// ---------------------------------------------------------------------------------------
// Fixtures. Every measure is a STRING, because that is how it crosses the wire: Postgres
// `numeric` -> Pydantic `Decimal` -> a JSON string (api.ts's standing rule). A fixture that
// used `2400` instead of `'2400'` would silently excuse an implementation that never parses.
// ---------------------------------------------------------------------------------------

const PLAN: Plan = {
  id: '3f6d5a10-0000-4000-8000-000000000000',
  status: 'active',
  starts_on: '2026-08-24',
  ends_on: '2026-09-20',
  weeks: 4,
  // Row 10's approved ruling: no goal is the normal state for a new user, and `null` is a
  // legitimate stored value here — not `''`.
  goal_snapshot: null,
  progression_note: 'Add 5 lb to the main lift each week; hold the accessory load.',
  calories_target: '2400',
  protein_g_target: '180',
  carbs_g_target: '250',
  fat_g_target: '80',
  created_at: '2026-08-24T12:00:00Z',
  days: [
    {
      id: '3f6d5a11-0000-4000-8000-000000000000',
      day_date: '2026-08-24',
      week_number: 1,
      focus: 'upper push',
      items: [
        {
          id: '3f6d5a12-0000-4000-8000-000000000000',
          exercise_id: '3f6d5a13-0000-4000-8000-000000000000',
          exercise_name: 'bench press',
          set_number: 1,
          reps: 8,
          weight_kg: '61.235',
        },
      ],
    },
  ],
}

/**
 * Today's logged intake as the API sends it. Strings, same reason as above.
 *
 * None of these numbers is 75 or 0.75 on purpose: those are what 1800/2400 and 60/80 look
 * like once someone turns row 18's two numbers into the one percentage it forbids, and the
 * assertions below hunt for exactly those values.
 */
const LOGGED_1800 = {
  calories: '1800',
  protein_g: '150',
  carbs_g: '180',
  fat_g: '60',
}

// ---------------------------------------------------------------------------------------
// Assertion helpers, and their self-tests.
//
// This project has shipped a marker assertion that could not fail (`_substance_hits` scored
// a `rest` hit inside "inte**rest**ed", so content-free replies passed the two rows written
// to catch content-free replies). Every helper below is therefore run against must-PASS and
// must-FAIL examples in this same file, BEFORE anything trusts it.
// ---------------------------------------------------------------------------------------

/** Split an object key into words: 'caloriesPct' -> ['calories','pct']. */
function keyWords(key: string): string[] {
  return key
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .split(/[^A-Za-z0-9]+/)
    .filter((word) => word.length > 0)
    .map((word) => word.toLowerCase())
}

/**
 * The CATEGORY "this field is a percentage", as whole words — never as a bare substring.
 * `duration` CONTAINS 'ratio'; `progression_note` contains 'progress'. Substring matching
 * here would be the `interested`/`rest` bug again, so the check is on words, not letters.
 */
const PERCENTAGE_WORDS = new Set(['percent', 'percentage', 'pct', 'ratio', 'ratios', 'fraction'])

/** Every key path in `value` whose name says "percentage". Empty list = clean. */
function percentageShapedKeys(value: unknown, path = ''): string[] {
  if (Array.isArray(value)) {
    return value.flatMap((item, index) => percentageShapedKeys(item, `${path}[${index}]`))
  }
  if (typeof value !== 'object' || value === null) return []

  return Object.entries(value).flatMap(([key, child]) => {
    const here = path === '' ? key : `${path}.${key}`
    const named = keyWords(key).some((word) => PERCENTAGE_WORDS.has(word))
    return [...(named ? [here] : []), ...percentageShapedKeys(child, here)]
  })
}

/** Every number anywhere inside `value`, however deeply nested. */
function numbersIn(value: unknown): number[] {
  if (typeof value === 'number') return [value]
  if (Array.isArray(value)) return value.flatMap(numbersIn)
  if (typeof value !== 'object' || value === null) return []
  return Object.values(value).flatMap(numbersIn)
}

describe('the helpers this file grades row 18 with (self-test, both directions)', () => {
  it('flags percentage-named fields', () => {
    expect(percentageShapedKeys({ percent: 75 })).toEqual(['percent'])
    expect(percentageShapedKeys({ caloriesPct: 75 })).toEqual(['caloriesPct'])
    expect(percentageShapedKeys({ target_ratio: 0.75 })).toEqual(['target_ratio'])
    expect(percentageShapedKeys({ macros: { percentage: 75 } })).toEqual(['macros.percentage'])
    expect(percentageShapedKeys([{ fraction: 0.75 }])).toEqual(['[0].fraction'])
  })

  it('does NOT flag ordinary fields that merely contain those letters', () => {
    // 'duration' contains the letters r-a-t-i-o. A substring matcher would flag it, and a
    // matcher that flags everything is not a matcher.
    expect(percentageShapedKeys({ duration: 45 })).toEqual([])
    expect(percentageShapedKeys({ progression_note: 'add 5 lb' })).toEqual([])
    expect(percentageShapedKeys({ calories: 1800, target: 2400 })).toEqual([])
    expect(percentageShapedKeys({ kind: 'target' })).toEqual([])
  })

  it('collects nested numbers and ignores numeric-looking strings', () => {
    expect(numbersIn({ a: 1, b: { c: [2, 3] }, d: '4', e: null })).toEqual([1, 2, 3])
    expect(numbersIn({ a: '1800' })).toEqual([])
    expect(numbersIn({})).toEqual([])
  })
})

// =======================================================================================
// SECTION C — diet targets (rows 18, 19, 21)
// =======================================================================================

describe('dietView', () => {
  // AC row 18: plan targets 2400 kcal, user logs 1800 -> /diet shows 1800 / 2400. TWO
  // NUMBERS. The row's own words: "never a percentage rounded to 0".
  it('reports logged intake and the target as two separate numbers', () => {
    const view = dietView({ plan: PLAN, consumed: LOGGED_1800 })

    expect(view.kind).toBe('target')
    expect(view.consumed.calories).toBe(1800)
    expect(view.kind === 'target' && view.target.calories).toBe(2400)
  })

  // AC row 18 (the half the row exists for): the view carries NO percentage — not under a
  // percentage-shaped name, and not as the VALUE a percentage would have. 1800/2400 is 75%
  // (and 0.75); so, coincidentally, is 60 g of fat against a 80 g target. If either number
  // appears anywhere in the view, someone computed the thing the row forbids.
  it('carries no percentage anywhere — not as a field name, not as a value', () => {
    const view = dietView({ plan: PLAN, consumed: LOGGED_1800 })

    expect(percentageShapedKeys(view)).toEqual([])
    expect(numbersIn(view)).not.toContain(75)
    expect(numbersIn(view)).not.toContain(0.75)
  })

  // AC row 18 (the NaN seatbelt `parseNumeric` exists for): a NaN that reaches the screen
  // renders as "NaN" or as nothing at all, and "nothing at all" is indistinguishable from
  // "no data" — the same failure-reads-as-empty-state shape rows 22/23 are about.
  it('emits only finite numbers', () => {
    const view = dietView({ plan: PLAN, consumed: LOGGED_1800 })

    expect(numbersIn(view).every(Number.isFinite)).toBe(true)
  })

  // AC row 19: no active plan -> logged intake with NO TARGET LINE. Never a target of 0.
  // This is the null-vs-zero doctrine, the one whose violation shipped as "peak 0 lb" on
  // /trends: we do not have a target, and 0 is not a way of saying that.
  it('shows intake with no target line at all when there is no active plan', () => {
    const view = dietView({ plan: null, consumed: LOGGED_1800 })

    expect(view.kind).toBe('no-target')
    expect(view.consumed.calories).toBe(1800)
    // Not "a target that happens to be null" — no target key on this state at all.
    expect('target' in view).toBe(false)
    // And no 0 smuggled in anywhere as a stand-in for the target we do not have.
    expect(numbersIn(view)).not.toContain(0)
  })

  // AC row 21: plan exists, nothing logged today -> 0 / 2400, WITH the target line. Zero is
  // the honest answer here: we know the user has logged nothing today, which is a fact, not
  // an absence of one. Row 19's null and row 21's zero are different claims about the world.
  it('shows 0 against the target when a plan exists and nothing is logged yet', () => {
    const view = dietView({ plan: PLAN, consumed: null })

    expect(view.kind).toBe('target')
    expect(view.consumed).toEqual({ calories: 0, protein_g: 0, carbs_g: 0, fat_g: 0 })
    expect(view.kind === 'target' && view.target.calories).toBe(2400)
    expect(view.kind === 'target' && view.target.protein_g).toBe(180)
    expect(view.kind === 'target' && view.target.carbs_g).toBe(250)
    expect(view.kind === 'target' && view.target.fat_g).toBe(80)
  })

  // AC rows 19 + 21 TOGETHER — the load-bearing one. Each row asserted alone still passes an
  // implementation that collapsed them (always render a target line; render 0 when there is
  // no plan), and that collapse IS the bug both rows were written to prevent. So assert the
  // distinction itself.
  it('keeps "no plan" and "a plan with nothing logged" distinguishable', () => {
    const noPlan = dietView({ plan: null, consumed: LOGGED_1800 })
    const nothingLogged = dietView({ plan: PLAN, consumed: null })

    expect(noPlan.kind).not.toBe(nothingLogged.kind)
    expect('target' in noPlan).toBe(false)
    expect('target' in nothingLogged).toBe(true)
  })

  // NO AC ROW — repo doctrine, flagged as such in the report.
  //
  // `parseNumeric`'s standing rule (lib/trends.ts): a value that is not a finite number
  // becomes `null`, never 0 and never NaN. 0 would be a false claim (a 0 kcal target) and
  // NaN renders as nothing. This is not in the approved table; it is the same null-vs-zero
  // doctrine rows 19/21 encode, applied to a malformed payload. If Toby wants a different
  // answer here, that is a new row, not a quiet edit to this test.
  it('parses a malformed target to null — never 0, never NaN (doctrine, no AC row)', () => {
    const view = dietView({ plan: { ...PLAN, calories_target: 'n/a' }, consumed: LOGGED_1800 })

    expect(view.kind === 'target' && view.target.calories).toBe(null)
    // The other targets on the same plan are unaffected: one bad field is not a bad plan.
    expect(view.kind === 'target' && view.target.protein_g).toBe(180)
    expect(numbersIn(view).every(Number.isFinite)).toBe(true)
  })
})

// =======================================================================================
// SECTION D — the /plan screen's states (rows 22, 23, 24)
// =======================================================================================

const IDLE_EMPTY = { loading: false, loadFailed: false, generating: false, plan: null }

describe('planView', () => {
  // AC row 22: /plan while fetching -> a loading state VISIBLY DISTINCT from empty. #43's
  // bug is exactly this collapse, and a fetch-in-flight rendering as "you have no plan"
  // invites the user to spend a Sonnet call generating a plan they already have.
  it('reports loading while the first fetch is in flight, distinct from empty', () => {
    const loading = planView({ ...IDLE_EMPTY, loading: true })
    const empty = planView(IDLE_EMPTY)

    expect(loading.kind).toBe('loading')
    expect(empty.kind).toBe('empty')
    expect(loading.kind).not.toBe(empty.kind)
  })

  // AC row 23: /plan fetch fails -> an error WITH A RETRY, never a silent "you have no
  // plan". A failed load rendering as the empty state reads as data loss — #18's original
  // bug, and the reason `listView`/`historyView`/`trendsView` all carry this exact state.
  it('reports load-failed with a retry, never the empty state', () => {
    const failed = planView({ ...IDLE_EMPTY, loadFailed: true })

    expect(failed.kind).toBe('load-failed')
    expect(failed.kind === 'load-failed' && failed.retry).toBe(true)
    expect(failed.kind).not.toBe(planView(IDLE_EMPTY).kind)
  })

  // AC row 24 (first half): POST /plans in flight -> "Bill is writing your plan…", a state
  // distinct from BOTH loading and empty. Three different sentences; three different states.
  it("reports generating, distinct from both loading and empty", () => {
    const generating = planView({ ...IDLE_EMPTY, generating: true })

    expect(generating.kind).toBe('generating')
    expect(generating.kind).not.toBe(planView({ ...IDLE_EMPTY, loading: true }).kind)
    expect(generating.kind).not.toBe(planView(IDLE_EMPTY).kind)
  })

  // AC row 24 (second half): "the button cannot be double-fired". Modelled as the view
  // itself withholding the submit affordance while a generation is in flight, so a second
  // dispatch is impossible from the view state — rather than as a handler-local guard the
  // node-env suite could never see.
  //
  // BOTH directions are asserted. `canGenerate: false` alone would pass an implementation
  // that hardcoded it false and left the user unable to ever generate anything; a flag that
  // cannot be true is not a flag.
  it('withholds the submit affordance while generating, and offers it when idle', () => {
    expect(planView({ ...IDLE_EMPTY, generating: true }).canGenerate).toBe(false)
    expect(planView(IDLE_EMPTY).canGenerate).toBe(true)
  })

  // AC rows 22/23/24 together — the assertion the three rows add up to, and the one that
  // fails if any pair is collapsed onto a single value. #43's bug is a collapse, so the
  // oracle asserts non-collapse directly rather than only asserting each shape.
  it('keeps all five states distinct from one another', () => {
    const kinds = [
      planView({ ...IDLE_EMPTY, loading: true }).kind,
      planView({ ...IDLE_EMPTY, loadFailed: true }).kind,
      planView({ ...IDLE_EMPTY, generating: true }).kind,
      planView(IDLE_EMPTY).kind,
      planView({ ...IDLE_EMPTY, plan: PLAN }).kind,
    ]

    expect(new Set(kinds).size).toBe(5)
  })

  // AC rows 22/23/24, the must-FAIL direction: without this, a `planView` that returned
  // 'loading' — or 'empty' — unconditionally would satisfy several of the tests above.
  it('reports the plan itself once it has loaded', () => {
    const view = planView({ ...IDLE_EMPTY, plan: PLAN })

    expect(view.kind).toBe('plan')
    expect(view.kind === 'plan' && view.plan).toBe(PLAN)
  })
})

// =======================================================================================
// THE REQUEST SEAMS — NO AC ROW. REPO DOCTRINE, DERIVED FROM #39 AND #40.
//
// These map to NO row in the 26-row table, and they are labelled that way on purpose. They
// exist because of a standing rule this project has now gotten wrong twice:
//
//   design.md — "Every date or time the user reads is rendered from `profiles.timezone`,
//   never the browser's." (#39 shipped an LA user's Aug-1 check-in reading "11:00 AM" on a
//   laptop set to Tokyo.)
//   #40 — the request seam is what makes that rule test-covered AT ALL. While the zone and
//   the window lived inline in a screen, swapping the zone for `null` left every test green.
//
// The expectations here are exactly that doctrine and nothing more. Anything beyond it —
// what /plan does with `today`, whether /diet may ask for more than one day — is Toby's
// call and belongs in the table, not invented here.
// =======================================================================================

describe('planRequest (repo doctrine, no AC row)', () => {
  // Doctrine: the USER's local today. At 2026-08-02 00:30 UTC an LA user is still on Aug 1,
  // and the plan's `starts_on` is decided from that day (row 9's browser-side counterpart).
  it("resolves today in the user's timezone, not the browser's", () => {
    expect(
      planRequest({ timezone: 'America/Los_Angeles' }, new Date('2026-08-02T00:30:00Z')),
    ).toEqual({ today: '2026-08-01' })
  })

  // Doctrine: the same UTC seatbelt `localToday` and the server's `local_today` both take.
  // A missing profile must not throw mid-render.
  it('falls back to UTC when there is no profile or no timezone', () => {
    expect(planRequest(null, new Date('2026-08-02T00:30:00Z'))).toEqual({ today: '2026-08-02' })
    expect(planRequest({ timezone: null }, new Date('2026-08-02T00:30:00Z'))).toEqual({
      today: '2026-08-02',
    })
  })
})

describe('dietRequest (repo doctrine, no AC row)', () => {
  // Doctrine: /diet is a TODAY screen — one day — and that day is the user's, not the
  // browser's. Same instant as above, same answer: Aug 1 in Los Angeles.
  it("asks for exactly one day, ending on the user's local today", () => {
    expect(
      dietRequest({ timezone: 'America/Los_Angeles' }, new Date('2026-08-02T00:30:00Z')),
    ).toEqual({ days: 1, today: '2026-08-01' })
  })

  it('falls back to UTC when there is no profile or no timezone', () => {
    expect(dietRequest(null, new Date('2026-08-02T00:30:00Z'))).toEqual({
      days: 1,
      today: '2026-08-02',
    })
    expect(dietRequest({ timezone: null }, new Date('2026-08-02T00:30:00Z'))).toEqual({
      days: 1,
      today: '2026-08-02',
    })
  })
})

// =======================================================================================
// THE API WIRE SHAPE — NO AC ROW. Same #40 doctrine: a URL, a verb and a body that no test
// names are three things a refactor can change in silence. Pinned here rather than in
// api.test.ts so this branch's oracle stays in one file.
// =======================================================================================

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function makeApi(response: Response) {
  const fetchMock = vi.fn(async () => response)
  const api = createApi({
    getToken: async () => 'token-abc',
    fetchFn: fetchMock as unknown as typeof fetch,
    baseUrl: 'http://api.test',
  })
  return { api, fetchMock }
}

describe('createPlan / getCurrentPlan (repo doctrine, no AC row)', () => {
  it('POSTs the requested week count to /plans and returns the parsed plan', async () => {
    const { api, fetchMock } = makeApi(jsonResponse(201, PLAN))

    await expect(api.createPlan(4)).resolves.toEqual(PLAN)

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://api.test/plans')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ weeks: 4 })
  })

  it('GETs /plans/current and returns the parsed plan', async () => {
    const { api, fetchMock } = makeApi(jsonResponse(200, PLAN))

    await expect(api.getCurrentPlan()).resolves.toEqual(PLAN)

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://api.test/plans/current')
    expect(init.method ?? 'GET').toBe('GET')
  })
})

// =======================================================================================
// AC ROW 20, RE-POINTED BY AMENDMENT 3 — the frontend half.
//
// https://github.com/tobyliu2004/coach-bill/issues/51#issuecomment-5389340006
//
// APPENDED, NEVER EDITED. Every line above is this branch's accepted oracle and stays
// byte-identical; this is a SECOND `import` from './api' rather than a change to the one at
// the top of the file, so the diff shows additions only.
//
// DIRECTION: NARROWING — these are new assertions on a seam that does not exist yet. They
// can only newly FAIL. Nothing above is relaxed and no row is reinterpreted downward.
//
// WHAT AMENDMENT 3 CHANGED. Row 20 — "three meals logged in a day -> SUMMED, not
// latest-wins" — is now served by `GET /trends?days=1`; /diet adds no endpoint of its own.
// The server does the summing: `nutrition` is ONE POINT PER DAY, already summed, and the
// series is SPARSE — a day with nothing logged is ABSENT, not a row of zeros or nulls. Same
// doctrine as `groupByDay` in lib/history.ts: a rendered empty day is a lie, because you
// didn't log nothing, you didn't log.
//
// So the DECISION row 20 lands on in the browser is: which point is today's, and what does
// its absence mean? That is `todaysIntake`, and it is two layers away from row 21:
//
//   layer 1 (here)     absent day  ->  null            "we have no intake for today"
//   layer 2 (dietView) null        ->  0 against 2400   rows 19/21's null-vs-zero doctrine
//
// Those layers are deliberately different, and the composition test at the end of this
// section pins the join between them. Collapsing layer 1 into a zero-filled object would
// make row 19 and row 21 indistinguishable one level lower down, where nothing else looks.
// =======================================================================================
import { type TrendsNutritionPoint } from './api'
import { todaysIntake } from './plan'

/**
 * A sparse, already-summed nutrition series exactly as `GET /trends?days=N` sends it.
 *
 * ORDERED SO THAT THREE ANSWERS DIFFER. Today is the 23rd, which is neither `[0]` (the
 * 24th) nor `.at(-1)` (the 22nd). An implementation that grabs either end of the array
 * instead of matching on `date` goes red here, and the calorie values differ per day so the
 * failure names which wrong element was taken.
 *
 * The 25th is deliberately ABSENT — that is what a day with nothing logged looks like on
 * the wire. There is no zero-filled row to find.
 */
const NUTRITION_SERIES: TrendsNutritionPoint[] = [
  { date: '2026-08-24', calories: '2600', protein_g: '190', carbs_g: '260', fat_g: '85' },
  { date: '2026-08-23', calories: '2000', protein_g: '160', carbs_g: '200', fat_g: '70' },
  { date: '2026-08-22', calories: '1700', protein_g: '140', carbs_g: '170', fat_g: '55' },
]

describe('todaysIntake (AC row 20, re-pointed by amendment 3)', () => {
  // The fixture's own legality, asserted rather than assumed. One point per day is a
  // SERVER-SIDE guarantee (the rollup groups by date), which is exactly why the client is
  // never allowed to sum: there is nothing to sum. If this ever fails, the fixture has
  // stopped describing a payload the API can produce and every assertion below is moot.
  it('is fed a legal payload: at most one nutrition point per day', () => {
    const dates = NUTRITION_SERIES.map((point) => point.date)

    expect(new Set(dates).size).toBe(dates.length)
  })

  // AC row 20: today's intake is the point whose `date` IS the requested day. Not the first
  // element, not the last. The series above is out of order on purpose so those three
  // answers are three different numbers.
  it("picks today's point by matching its date, never by position in the array", () => {
    expect(todaysIntake(NUTRITION_SERIES, '2026-08-23')).toEqual({
      calories: '2000',
      protein_g: '160',
      carbs_g: '200',
      fat_g: '70',
    })

    // And the two wrong answers are genuinely wrong — without this, a fixture whose ends
    // happened to hold the same numbers would let `[0]` pass. Stated as values, not as
    // indexes, because it is the NUMBER on screen that would be wrong.
    expect(todaysIntake(NUTRITION_SERIES, '2026-08-23')?.calories).not.toBe('2600')
    expect(todaysIntake(NUTRITION_SERIES, '2026-08-23')?.calories).not.toBe('1700')
  })

  // AC row 20: the point is ALREADY SUMMED by the server — three meals arrive as one point.
  // The client does no arithmetic here at all: the value that goes to the screen is the
  // value that came off the wire, byte for byte, still a string. Parsing happens once, in
  // `dietView`, via `parseNumeric`.
  it('returns the server-summed measures unchanged, as strings', () => {
    const intake = todaysIntake(NUTRITION_SERIES, '2026-08-24')

    expect(intake).toEqual({
      calories: '2600',
      protein_g: '190',
      carbs_g: '260',
      fat_g: '85',
    })
    // Strings, not numbers: a `number` here would mean someone parsed early, and early
    // parsing is how a Decimal becomes a float twice.
    expect(typeof intake?.calories).toBe('string')
  })

  // AC row 20 (the sparseness half, and the layer-1 end of the null-vs-zero seam): a day
  // with nothing logged is ABSENT from the series, and absence is `null`. NOT a zero-filled
  // object — that would be this module asserting "you ate 0 kcal" when the truth is "we
  // have no entry", which is the "peak 0 lb" bug one screen along.
  it('returns null for a day the sparse series has no point for', () => {
    expect(todaysIntake(NUTRITION_SERIES, '2026-08-25')).toBeNull()
    expect(todaysIntake([], '2026-08-23')).toBeNull()
  })

  // AC rows 20 + 21, THE JOIN. This is the assertion that shows the two layers are
  // deliberately different rather than accidentally the same: layer 1 says `null` ("no
  // entry"), and layer 2 turns that into `0 / 2400` WITH the target line ("you have a
  // target and have logged nothing against it yet"). Each layer tested alone would still
  // pass an implementation that zero-filled at layer 1 — and that implementation would make
  // row 19 and row 21 indistinguishable underneath `dietView`, where nothing else looks.
  it('feeds row 21: an absent day becomes 0 against the target, not a missing target', () => {
    const view = dietView({ plan: PLAN, consumed: todaysIntake(NUTRITION_SERIES, '2026-08-25') })

    expect(view.kind).toBe('target')
    expect(view.consumed).toEqual({ calories: 0, protein_g: 0, carbs_g: 0, fat_g: 0 })
    expect(view.kind === 'target' && view.target.calories).toBe(2400)
  })

  // AC rows 20 + 18, the other side of that join: a day that IS present flows through
  // unparsed, is parsed once by `dietView`, and lands as row 18's two numbers.
  it('feeds row 18: a present day becomes the logged number against the target', () => {
    const view = dietView({ plan: PLAN, consumed: todaysIntake(NUTRITION_SERIES, '2026-08-23') })

    expect(view.kind).toBe('target')
    expect(view.consumed.calories).toBe(2000)
    expect(view.kind === 'target' && view.target.calories).toBe(2400)
  })
})
