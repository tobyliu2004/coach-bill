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
    listCheckIns(): Promise<CheckIn[]> {
      return request<CheckIn[]>('/check-ins')
    },
    deleteCheckIn(id: string): Promise<void> {
      return request<void>(`/check-ins/${id}`, { method: 'DELETE' })
    },
  }
}

export type Api = ReturnType<typeof createApi>
