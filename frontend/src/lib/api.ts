/**
 * Typed wrapper for the Coach Bill backend API.
 *
 * createApi is a factory with injected token getter + fetch so the logic is unit-testable
 * (api.test.ts); the app-wired instance lives in client.ts. Tokens travel in the
 * Authorization header (no cookies), so CORS needs no credentials mode.
 */

/** Mirrors backend/app/schemas/profiles.py ProfileOut — keep them in sync. */
export interface Profile {
  id: string
  display_name: string | null
  weight_unit: 'lb' | 'kg'
  goal: string | null
  timezone: string | null
  consented_at: string | null
  created_at: string
}

/** Mirrors backend ProfileUpdate: only provided fields change; consent stamps consented_at. */
export interface ProfileUpdate {
  display_name?: string
  weight_unit?: 'lb' | 'kg'
  goal?: string
  timezone?: string
  consent?: boolean
}

/**
 * The facts extraction pulled out of a check-in. Mirrors the *Out models in
 * backend/app/schemas/check_ins.py — keep them in sync.
 *
 * Every number here is a STRING, not a `number`: the backend stores these as Postgres
 * `numeric` and Pydantic serializes `Decimal` to a JSON string, because JSON's only number
 * type is a float and 61.235 kg has no business being rounded by a parser. Treat them as
 * opaque display values; the day we do arithmetic in the browser, parse deliberately.
 */
export interface WorkoutSet {
  id: string
  exercise_name: string
  set_number: number
  reps: number
  /** null for bodyweight moves — never 0, which would be a real load of zero. */
  weight_kg: string | null
}

export interface NutritionEntry {
  id: string
  description: string
  calories: string
  protein_g: string
  carbs_g: string
  fat_g: string
  meal: 'breakfast' | 'lunch' | 'dinner' | 'snack' | null
}

export interface SleepEntry {
  id: string
  hours: string
  /** 1-5, null unless the user actually rated it. */
  quality: number | null
}

export interface BodyweightEntry {
  id: string
  weight_kg: string
}

export interface CheckInFacts {
  sets: WorkoutSet[]
  nutrition: NutritionEntry[]
  sleep: SleepEntry[]
  bodyweight: BodyweightEntry[]
}

/** Mirrors backend/app/schemas/coach.py CoachReplyOut — keep them in sync. */
export interface CoachReply {
  id: string
  /** Bill's reply, as stored. PLAIN TEXT — render it as text, never as HTML. It is model
   *  output, which is untrusted content going onto a page. */
  content: string
  created_at: string
}

/** Mirrors backend/app/schemas/check_ins.py CheckInOut — keep them in sync. */
export interface CheckIn {
  id: string
  raw_text: string
  source: 'voice' | 'text'
  entry_date: string
  created_at: string
  /**
   * 'pending' — saved, extraction unfinished (also what a crash leaves behind)
   * 'done'    — extraction ran and stored everything it found, INCLUDING nothing at all
   * 'partial' — a fact was found but had to be dropped (a rejected exercise name)
   * 'failed'  — extraction itself broke; the raw text is intact regardless
   */
  extraction_status: 'pending' | 'done' | 'partial' | 'failed'
  facts: CheckInFacts
  /**
   * Coach Bill's reply, bundled by the server so a reply can't vanish on refresh.
   *
   * `null` means "no reply" — which is NOT "the reply failed". Keeping those two apart is
   * `lib/coachView.ts`'s job, and collapsing them is the bug class the #18 retro named.
   */
  reply: CoachReply | null
}

/**
 * The computed rollup behind the Trends screen. Mirrors the models in
 * backend/app/schemas/trends.py — keep them in sync.
 *
 * Every measure is a STRING here for the same reason the per-check-in facts are: Postgres
 * `numeric` -> Pydantic `Decimal` -> a JSON string, because JSON's only number type is a
 * float. Unlike those, these ARE arithmetic inputs — so they are parsed exactly once, by
 * `parseNumeric` in lib/trends.ts, and never with a bare `Number(...)` scattered through JSX.
 */
export interface TrendsVolumePoint {
  date: string
  /** null — never '0' — on a day of purely unweighted work: no tonnage to measure. */
  volume_kg: string | null
  bodyweight_sets: number
  bodyweight_reps: number
}

export interface TrendsExerciseSummary {
  /** The canonical catalog name; aliases were resolved at write time (#19). */
  name: string
  /** Counts EVERY set, weighted or not... */
  sets: number
  reps: number
  /** ...while these two count only the weighted ones. Asymmetric on purpose. */
  volume_kg: string | null
  heaviest_kg: string | null
}

export interface TrendsSleepPoint {
  date: string
  hours: string
  quality: number | null
}

export interface TrendsBodyweightPoint {
  date: string
  weight_kg: string
}

export interface TrendsNutritionPoint {
  date: string
  calories: string
  protein_g: string
  carbs_g: string
  fat_g: string
}

/**
 * ONE PLANNED SET. Mirrors the backend's `PlanItemOut`, which mirrors `plan_items`, which
 * mirrors `workout_sets` — one row per set all the way down, so planned-vs-actual stays a
 * join rather than a parser.
 */
export interface PlanItem {
  id: string
  exercise_id: string
  /** The CANONICAL catalog name — aliases were resolved server-side at write time. */
  exercise_name: string
  set_number: number
  reps: number
  /** A STRING, or null for a movement with no external load. Never 0 — 0 would say the
   *  user is planned to lift nothing. Parsed once, deliberately, via `parseNumeric`. */
  weight_kg: string | null
}

export interface PlanDay {
  id: string
  day_date: string
  week_number: number
  /** Never empty, and a rest day says rest. A day whose every item was dropped keeps its
   *  training focus rather than being relabelled — #51 row 4. */
  focus: string
  /** Did the user actually train on this date? COMPUTED server-side from their own
   *  workouts (#51 row 13), never stored, so it cannot go stale. */
  logged: boolean
  items: PlanItem[]
}

export interface Plan {
  id: string
  status: string
  starts_on: string
  ends_on: string
  weeks: number
  /** NULL is legitimate — no goal is the normal state for a new user (#51 row 10). Never
   *  `""`, so "they had no goal" stays distinguishable from "their goal was blank". */
  goal_snapshot: string | null
  progression_note: string
  /** Every measure crosses the wire as a STRING: Postgres `numeric` -> Pydantic `Decimal`
   *  -> a JSON string, because JSON's only number type is a float and a calorie target is
   *  not something a parser gets to round. `lib/plan.ts` parses them once. */
  calories_target: string
  protein_g_target: string
  carbs_g_target: string
  fat_g_target: string
  created_at: string
  days: PlanDay[]
}

export interface Trends {
  /** The window the SERVER resolved. The chart's axis is laid out from these, so the
   *  client never recomputes "today" — the drift issue #40 is about. Present even when
   *  every series is empty, so the screen can say WHICH window was empty. */
  start_date: string
  end_date: string
  /** Sparse: a day with nothing logged is absent, not a row of nulls. Oldest first —
   *  the opposite of listCheckIns, on purpose. A chart reads left to right; a log reads
   *  backwards. */
  volume: TrendsVolumePoint[]
  exercises: TrendsExerciseSummary[]
  sleep: TrendsSleepPoint[]
  bodyweight: TrendsBodyweightPoint[]
  nutrition: TrendsNutritionPoint[]
}

/** No session, or the backend rejected the token — the caller should treat as signed out. */
export class ApiAuthError extends Error {}

/** Any other non-2xx backend response. */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

interface ApiDeps {
  getToken: () => Promise<string | null>
  fetchFn?: typeof fetch
  baseUrl?: string
}

export function createApi({
  getToken,
  fetchFn = fetch,
  baseUrl = import.meta.env.VITE_API_URL ?? 'http://localhost:8001',
}: ApiDeps) {
  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const token = await getToken()
    if (!token) throw new ApiAuthError('no active session')

    const response = await fetchFn(`${baseUrl}${path}`, {
      ...init,
      headers: {
        ...init?.headers,
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
    })
    if (response.status === 401) throw new ApiAuthError('session rejected by the API')
    if (!response.ok) throw new ApiError(response.status, `${path} failed`)
    // 204 (e.g. DELETE) has no body — calling .json() on it throws. Return void.
    if (response.status === 204) return undefined as T
    return (await response.json()) as T
  }

  return {
    getMe(): Promise<Profile> {
      return request<Profile>('/me')
    },
    updateMe(patch: ProfileUpdate): Promise<Profile> {
      return request<Profile>('/me', { method: 'PATCH', body: JSON.stringify(patch) })
    },
    createCheckIn(text: string): Promise<CheckIn> {
      return request<CheckIn>('/check-ins', { method: 'POST', body: JSON.stringify({ text }) })
    },
    /**
     * The caller's check-ins. With no argument: today only — the backend's own default.
     *
     * `days` is still left off the URL entirely when it wasn't asked for, rather than sent
     * as `?days=1`. The two are equivalent to the server.
     *
     * HISTORICAL NOTE, so this comment doesn't quietly become false: the bare form existed
     * because it made /app's traffic byte-identical across PR 1's refactor. As of #40 that
     * is no longer true — /app asks for `todayRequest(...).days`, so it now sends `?days=1`
     * explicitly. That was the point: a window the screen never names is a window no test
     * can pin. The no-argument form stays because it is the honest default for any caller
     * that genuinely means "today".
     */
    listCheckIns(days?: number): Promise<CheckIn[]> {
      return request<CheckIn[]>(days === undefined ? '/check-ins' : `/check-ins?days=${days}`)
    },
    /**
     * The caller's computed trends over their last `days` local days.
     *
     * `days` is always sent, unlike `listCheckIns`' optional one: there is no pre-existing
     * request shape to keep byte-identical here, and being explicit means the URL says what
     * window is on screen.
     */
    getTrends(days: number): Promise<Trends> {
      return request<Trends>(`/trends?days=${days}`)
    },
    /**
     * Ask Coach Bill to reply to one check-in.
     *
     * Takes no body: the text being replied to is the check-in already on the server, so
     * there is nothing to send and nothing that could disagree with what was stored.
     *
     * Safe to call twice. The endpoint is get-or-create — 201 the first time, 200 with the
     * same reply after — so a double-click or a remount costs one round trip rather than a
     * second Sonnet call and a duplicate reply. Both are 2xx, so this resolves either way
     * and the caller doesn't branch on which happened.
     */
    requestReply(checkInId: string): Promise<CoachReply> {
      return request<CoachReply>(`/check-ins/${checkInId}/reply`, {
        method: 'POST',
        // A HARD CLIENT-SIDE BOUND, independent of the server's own.
        //
        // The server caps generation at 60s (REPLY_DEADLINE_SECONDS), so this only fires if
        // something upstream of that stalls — a hung connection, a proxy holding the socket.
        // Without it a bare `fetch` waits forever and the user sits watching "Bill is
        // reading your check-in…" with no way out. 70s is deliberately just above the
        // server's bound, so in every normal failure the server's 503 wins the race and the
        // user gets the real error rather than a generic abort.
        signal: AbortSignal.timeout(70_000),
      })
    },
    /**
     * Generate and store a program for the caller.
     *
     * NO CLIENT TIMEOUT, unlike `requestReply`. The server's own planner budget is 60s and
     * a plan is a bigger generation than a reply; the screen shows "Bill is writing your
     * plan…" and the button is withheld while it runs (row 24), so there is no way for the
     * user to pile on requests while they wait.
     *
     * Not idempotent, and deliberately not pretending to be: a second call generates a
     * second plan and archives the first. The DATABASE guarantees only one is active
     * (a partial unique index), and two CONCURRENT calls converge on the same plan — but
     * they each spend a model call, which is why the view withholds the button rather than
     * relying on the server to absorb a double-click.
     */
    createPlan(weeks: number): Promise<Plan> {
      return request<Plan>('/plans', { method: 'POST', body: JSON.stringify({ weeks }) })
    },
    /**
     * The caller's active plan.
     *
     * Throws `ApiError` with status 404 when there is none — which is a NORMAL state, not a
     * failure, and the screen must tell the two apart. `planView` is where that decision
     * lives: a 404 is `empty` (you have no plan yet), any other failure is `load-failed`
     * (with a retry). Collapsing them is #43's bug and rows 22/23 exist to stop it.
     */
    getCurrentPlan(): Promise<Plan> {
      return request<Plan>('/plans/current')
    },
    deleteCheckIn(id: string): Promise<void> {
      return request<void>(`/check-ins/${id}`, { method: 'DELETE' })
    },
  }
}

export type Api = ReturnType<typeof createApi>
