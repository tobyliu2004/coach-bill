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

// --- history window (issue #20, PR 1) ---
//
// Appended, not edited: every assertion above is #18/#19's oracle and stays exactly as it
// was. `listCheckIns` gains an OPTIONAL `days` argument here; the block below pins both
// halves of that optionality, because only one of them is a new feature — the other is a
// regression guard on the screen that already ships.

describe('listCheckIns — the days window', () => {
  // AC row 1 (REGRESSION GUARD, client half): today's no-argument call must still produce
  // exactly `/check-ins`. A wrapper that helpfully defaults to `?days=1` would change the
  // URL the existing /app screen sends — the one thing row 1 forbids.
  it('sends no query string at all when called with no argument', async () => {
    const { api, fetchMock } = makeApi({ token: 't', response: jsonResponse(200, [CHECK_IN]) })

    await api.listCheckIns()

    const [url] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://api.test/check-ins')
    expect(url).not.toContain('days')
  })

  // AC rows 2/7/8 (client half — the transport for the window): the number of days the
  // caller asked for has to reach the server as `?days=N`. Without this the /history
  // screen silently renders today only, and every window row above is untestable in the UI.
  it('sends ?days=N when a window is requested', async () => {
    const { api, fetchMock } = makeApi({ token: 't', response: jsonResponse(200, [CHECK_IN]) })

    await api.listCheckIns(30)

    const [url] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://api.test/check-ins?days=30')
  })

  // AC row 1 (the other side of the guard): an EXPLICIT 1 is not the same call as no
  // argument — it carries the param. Asserting only the no-arg case would pass against a
  // wrapper that dropped `days` whenever it equalled 1, which would quietly cap /history
  // at a single day the moment someone passed a variable that happened to be 1.
  it('sends ?days=1 when 1 is passed explicitly', async () => {
    const { api, fetchMock } = makeApi({ token: 't', response: jsonResponse(200, [CHECK_IN]) })

    await api.listCheckIns(1)

    const [url] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://api.test/check-ins?days=1')
  })

  // AC rows 2/7/8 (client half): the window call is still authenticated and still a GET.
  it('keeps the Bearer header and the GET method on a windowed call', async () => {
    const { api, fetchMock } = makeApi({ token: 't', response: jsonResponse(200, []) })

    await api.listCheckIns(7)

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://api.test/check-ins?days=7')
    expect(init.method ?? 'GET').toBe('GET')
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer t')
  })
})

// --- trends (issue #20, PR 2 — row 36) ---
//
// Appended, not edited: every assertion above is #18/#19/PR 1's oracle and stays exactly as
// it was. A second `import` from './api' rather than a change to the one at the top of the
// file, so the diff against the oracle commit shows additions only.
//
// This is the transport. Without it every backend row above is unreachable from the UI, and
// every frontend row is testing a function nothing calls.

import { type Trends } from './api'

// Typed as `Trends` on purpose: every measure below is a STRING because the backend stores
// these as Postgres `numeric` and Pydantic serialises `Decimal` to a JSON string (decision
// 7). If the mirrored interface ever declares them as `number`, this fixture stops
// type-checking and `npm run build` fails — which is the only place that drift is catchable,
// since types are erased before vitest runs.
const TRENDS: Trends = {
  start_date: '2026-07-03',
  end_date: '2026-08-01',
  volume: [{ date: '2026-08-01', volume_kg: '3200', bodyweight_sets: 0, bodyweight_reps: 0 }],
  exercises: [{ name: 'squat', sets: 12, reps: 96, volume_kg: '9180', heaviest_kg: '140' }],
  sleep: [{ date: '2026-08-01', hours: '8', quality: 4 }],
  bodyweight: [{ date: '2026-08-01', weight_kg: '81.25' }],
  nutrition: [
    {
      date: '2026-08-01',
      calories: '2000',
      protein_g: '140',
      carbs_g: '200',
      fat_g: '60',
    },
  ],
}

describe('getTrends', () => {
  // AC row 36: api.getTrends(30) issues GET /trends?days=30 with the Bearer header, and
  // returns the parsed payload. The URL is pinned as a literal — a wrapper that dropped the
  // query string would silently render the backend's default window and every window row
  // would be untestable from the UI.
  it('GETs /trends?days=30 with the Bearer header and returns the parsed payload', async () => {
    const { api, fetchMock } = makeApi({ token: 't', response: jsonResponse(200, TRENDS) })

    const result = await api.getTrends(30)

    expect(result).toEqual(TRENDS)
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://api.test/trends?days=30')
    expect(init.method ?? 'GET').toBe('GET')
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer t')
  })

  // AC row 36: the argument actually reaches the URL. Asserting only the 30 case would pass
  // against a wrapper that hardcoded `?days=30` and ignored its parameter.
  it('sends the days it was given, not a hardcoded window', async () => {
    const { api, fetchMock } = makeApi({ token: 't', response: jsonResponse(200, TRENDS) })

    await api.getTrends(7)

    const [url] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://api.test/trends?days=7')
  })

  // AC row 36 + backend row 7: an empty window is a normal 200 payload, not a rejection —
  // the wrapper must resolve with the window and five empty series so the screen can render
  // 'empty' (frontend row 33) rather than 'load-failed'.
  it('resolves with the empty-window payload rather than treating it as a failure', async () => {
    const empty: Trends = {
      start_date: '2026-07-03',
      end_date: '2026-08-01',
      volume: [],
      exercises: [],
      sleep: [],
      bodyweight: [],
      nutrition: [],
    }
    const { api } = makeApi({ token: 't', response: jsonResponse(200, empty) })

    await expect(api.getTrends(30)).resolves.toEqual(empty)
  })

  // AC row 36 (the auth boundary, shared with the plumbing block at the top): a 401 from
  // /trends is an ApiAuthError, which is what frontend row 32 turns into a sign-out.
  it('throws ApiAuthError when the backend rejects the token', async () => {
    const { api } = makeApi({ token: 't', response: jsonResponse(401, { detail: 'nope' }) })

    await expect(api.getTrends(30)).rejects.toBeInstanceOf(ApiAuthError)
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
