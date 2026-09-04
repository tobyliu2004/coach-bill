// Keeps the Render Free API warm. Measured against prod: a cold GET /health/db took
// 32.5s on 2026-09-02 and 22.3s on 2026-09-03, against 0.20s warm. Render spins the
// service down after 15 min idle, so without this the API is cold for nearly every
// first visit of the day and the app reads as broken rather than slow.
//
// This pings and logs. Nothing else, deliberately: a keep-alive that does more than
// ping is a keep-alive that can fail for its own reasons.
//
// HOW IT REPORTS FAILURE — the part that has to be TRUE rather than merely claimed.
// An earlier version of this file said "a keep-alive that fails silently is worse
// than none" and then was exactly that, in three separate ways: it logged res.status
// without checking it (so a 503 — which is what /health/db returns when Postgres is
// unreachable — read identically to a 200), it swallowed the error so the returned
// promise always resolved (so Cloudflare recorded every invocation as OK and the
// error-rate metric that Notifications key off never moved), and it declared no
// [observability], so the log lines themselves went nowhere durable. A total outage
// of the API would have produced an unbroken wall of green cron history.
//
// So now: anything that is not 2xx is a failure, and every failure is logged AND
// rethrown, because a rejected promise is the one signal the platform can alert on.
//
// The GitHub Action in .github/workflows/keepalive.yml remains the backstop that
// actively emails on failure; this Worker's job is to be warm and to be honest.
//
// NOTE: api.coachbill.fit is DNS-only (grey cloud), so this fetch goes straight to
// Render rather than looping back through the zone. That also means no Cloudflare
// cache sits in front of it — an earlier `cache-control: no-cache` request header
// here claimed to prevent a cached 200 masking a sleeping service, but Cloudflare
// ignores that header on incoming requests, so it never did. Removed rather than
// left as a comforting lie; `cf: { cacheTtl: 0 }` is the real lever if the record is
// ever orange-clouded.

const HEALTH_URL = 'https://api.coachbill.fit/health/db'

// A cold start is ~33s, so the ceiling has to clear that with room. Without any bound
// a hung connection would sit here until the runtime killed it with no log line.
const TIMEOUT_MS = 90_000

/** The one field of the cron event this handler reads. */
interface ScheduledEvent {
  readonly cron: string
}

async function ping(cron: string): Promise<void> {
  const started = Date.now()
  try {
    const res = await fetch(HEALTH_URL, { signal: AbortSignal.timeout(TIMEOUT_MS) })
    const elapsed = Date.now() - started
    // "We got a response" is not "the API is healthy": /health/db answers 503 when the
    // database is unreachable (backend/app/routes/health.py), and Render answers 404
    // for a renamed service. Both are failures and neither throws on its own.
    if (!res.ok) throw new Error(`HTTP ${res.status} in ${elapsed}ms`)
    console.log(`[${cron}] ${HEALTH_URL} -> ${res.status} in ${elapsed}ms`)
  } catch (err) {
    const elapsed = Date.now() - started
    const reason = err instanceof Error ? `${err.name}: ${err.message}` : String(err)
    console.error(`[${cron}] ${HEALTH_URL} -> FAILED after ${elapsed}ms — ${reason}`)
    // Rethrow. Logging alone leaves the invocation recorded as a success, which is the
    // silent-failure mode this Worker is written to avoid.
    throw err
  }
}

export default {
  // RETURN the promise rather than handing it to ctx.waitUntil. Returning it makes the
  // runtime wait for completion just the same, and additionally lets a rejection mark
  // the cron invocation failed — waitUntil keeps it alive but swallows the outcome,
  // which is the entire signal.
  scheduled(event: ScheduledEvent): Promise<void> {
    return ping(event.cron)
  },
}
