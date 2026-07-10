/**
 * The backend API wrapper contract, specified before implementation.
 *
 * createApi takes an injected token getter + fetch so the tests never touch the real
 * supabase client or network.
 */
import { describe, expect, it, vi } from 'vitest'
import { ApiAuthError, ApiError, createApi, type Profile } from './api'

const PROFILE: Profile = {
  id: '5e0acb28-0000-4000-8000-000000000000',
  display_name: 'Toby',
  weight_unit: 'lb',
  goal: null,
  timezone: null,
  consented_at: null,
  created_at: '2026-07-01T12:00:00Z',
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function makeApi(opts: { token?: string | null; response?: Response }) {
  const fetchMock = vi.fn(async () => opts.response ?? jsonResponse(200, PROFILE))
  const api = createApi({
    getToken: async () => opts.token ?? null,
    fetchFn: fetchMock as unknown as typeof fetch,
    baseUrl: 'http://api.test',
  })
  return { api, fetchMock }
}

describe('authentication plumbing', () => {
  it('attaches the access token as a Bearer header', async () => {
    const { api, fetchMock } = makeApi({ token: 'token-abc' })

    await api.getMe()

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://api.test/me')
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer token-abc')
  })

  it('throws ApiAuthError without calling the network when there is no session', async () => {
    const { api, fetchMock } = makeApi({ token: null })

    await expect(api.getMe()).rejects.toBeInstanceOf(ApiAuthError)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('throws ApiAuthError on a 401 (token rejected by the backend)', async () => {
    const { api } = makeApi({ token: 't', response: jsonResponse(401, { detail: 'nope' }) })

    await expect(api.getMe()).rejects.toBeInstanceOf(ApiAuthError)
  })
})

describe('getMe', () => {
  it('returns the parsed profile', async () => {
    const { api } = makeApi({ token: 't' })

    await expect(api.getMe()).resolves.toEqual(PROFILE)
  })

  it('throws ApiError carrying the status for non-auth failures', async () => {
    const { api } = makeApi({ token: 't', response: jsonResponse(500, { detail: 'boom' }) })

    const err = await api.getMe().catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(500)
  })
})

describe('updateMe', () => {
  it('PATCHes JSON and returns the updated profile', async () => {
    const updated = { ...PROFILE, goal: 'cut to 175' }
    const { api, fetchMock } = makeApi({ token: 't', response: jsonResponse(200, updated) })

    const result = await api.updateMe({ goal: 'cut to 175', consent: true })

    expect(result.goal).toBe('cut to 175')
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://api.test/me')
    expect(init.method).toBe('PATCH')
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json')
    expect(JSON.parse(init.body as string)).toEqual({ goal: 'cut to 175', consent: true })
  })
})
