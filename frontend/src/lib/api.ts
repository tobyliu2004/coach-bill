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
    return (await response.json()) as T
  }

  return {
    getMe(): Promise<Profile> {
      return request<Profile>('/me')
    },
    updateMe(patch: ProfileUpdate): Promise<Profile> {
      return request<Profile>('/me', { method: 'PATCH', body: JSON.stringify(patch) })
    },
  }
}

export type Api = ReturnType<typeof createApi>
