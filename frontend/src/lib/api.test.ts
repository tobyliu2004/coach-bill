/**
 * The backend API wrapper contract, specified before implementation.
 *
 * createApi takes an injected token getter + fetch so the tests never touch the real
 * supabase client or network.
 */
import { describe, expect, it, vi } from 'vitest'
import { ApiAuthError, ApiError, createApi, type CheckIn, type Profile } from './api'

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

// --- check-ins wrapper (backend issue #18) ---

const CHECK_IN: CheckIn = {
  id: '9a3b1c2d-0000-4000-8000-000000000000',
  raw_text: 'did 5x5 squats at 225',
  source: 'text',
  entry_date: '2026-07-15',
  created_at: '2026-07-15T12:00:00Z',
  // Added by issue #19: every CheckIn the API returns now carries these, so a fixture
  // without them isn't a CheckIn. Fixture only — this file tests the HTTP wrapper (URL,
  // method, headers, parsing), and no assertion below reads either field.
  extraction_status: 'done',
  facts: { sets: [], nutrition: [], sleep: [], bodyweight: [] },
}

describe('createCheckIn', () => {
  // AC row 1: POST /check-ins with a JSON { text } body + Bearer header, returns the CheckIn.
  it('POSTs the text as JSON and returns the parsed check-in', async () => {
    const { api, fetchMock } = makeApi({ token: 't', response: jsonResponse(201, CHECK_IN) })

    const result = await api.createCheckIn('did 5x5 squats at 225')

    expect(result).toEqual(CHECK_IN)
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://api.test/check-ins')
    expect(init.method).toBe('POST')
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json')
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer t')
    expect(JSON.parse(init.body as string)).toEqual({ text: 'did 5x5 squats at 225' })
  })
})

describe('listCheckIns', () => {
  // AC rows 7/8: GET /check-ins returns the parsed array (including the empty-list case).
  it('GETs and returns the parsed array', async () => {
    const { api, fetchMock } = makeApi({ token: 't', response: jsonResponse(200, [CHECK_IN]) })

    const result = await api.listCheckIns()

    expect(result).toEqual([CHECK_IN])
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://api.test/check-ins')
    // GET is the default method; assert it is not mutated to something else.
    expect(init.method ?? 'GET').toBe('GET')
  })

  it('returns an empty array when the backend has no check-ins today', async () => {
    const { api } = makeApi({ token: 't', response: jsonResponse(200, []) })

    await expect(api.listCheckIns()).resolves.toEqual([])
  })
})

describe('deleteCheckIn', () => {
  // AC row 11: DELETE /check-ins/{id} to the right path + method with a Bearer header.
  it('DELETEs the id path with the Bearer header', async () => {
    const { api, fetchMock } = makeApi({
      token: 't',
      response: new Response(null, { status: 204 }),
    })

    await api.deleteCheckIn('9a3b1c2d-0000-4000-8000-000000000000')

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://api.test/check-ins/9a3b1c2d-0000-4000-8000-000000000000')
    expect(init.method).toBe('DELETE')
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer t')
  })

  // AC row 11 (204, no body): the current request<T> always calls response.json(), which
  // THROWS on an empty 204 body. deleteCheckIn must RESOLVE, not reject. This test is
  // expected to fail against today's api.ts — it pins the 204-handling fix.
  it('resolves (does not reject) on an empty 204 response', async () => {
    const { api } = makeApi({ token: 't', response: new Response(null, { status: 204 }) })

    await expect(api.deleteCheckIn('9a3b1c2d-0000-4000-8000-000000000000')).resolves.toBeUndefined()
  })
})
