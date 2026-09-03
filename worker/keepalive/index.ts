// Keeps the Render Free API warm. Measured 2026-09-02: a cold GET /health/db took
// 32.5s. Render spins the service down after 15 min idle, so without this the API is
// cold for nearly every first visit of the day, and the app reads as broken.
//
// This pings and logs. Nothing else, deliberately: a keep-alive that does more than
// ping is a keep-alive that can fail for its own reasons, and a keep-alive that fails
// silently is worse than none. The GitHub Action in .github/workflows/keepalive.yml
// stays on as the backstop — a red scheduled workflow is the free monitoring this
// Worker's silent success will never give us.
//
// NOTE: api.coachbill.fit is DNS-only (grey cloud), so this fetch goes straight to
// Render rather than looping back through the zone. Verify from Render's logs.

const HEALTH_URL = 'https://api.coachbill.fit/health/db'

// A cold start is ~33s, so the ceiling has to clear that with room; without any
// bound a hung connection would sit here until the runtime kills it with no log line.
const TIMEOUT_MS = 90_000

// Minimal shapes for the two runtime values we touch. Declared locally rather than
// pulling in @cloudflare/workers-types: this directory has no package.json and no
// build step, and two fields do not justify creating one.
interface ScheduledEvent {
  readonly cron: string
}
interface ExecutionContext {
  waitUntil(promise: Promise<unknown>): void
}

async function ping(cron: string): Promise<void> {
  const started = Date.now()
  try {
    const res = await fetch(HEALTH_URL, {
      signal: AbortSignal.timeout(TIMEOUT_MS),
      // Render caches nothing here, but be explicit: a cached 200 would keep the
      // Worker green while the service slept.
      headers: { 'cache-control': 'no-cache' },
    })
    const elapsed = Date.now() - started
    console.log(`[${cron}] ${HEALTH_URL} -> ${res.status} in ${elapsed}ms`)
  } catch (err) {
    const elapsed = Date.now() - started
    const reason = err instanceof Error ? `${err.name}: ${err.message}` : String(err)
    console.error(`[${cron}] ${HEALTH_URL} -> failed after ${elapsed}ms — ${reason}`)
  }
}

export default {
  scheduled(event: ScheduledEvent, _env: unknown, ctx: ExecutionContext): void {
    // waitUntil keeps the invocation alive for the full request; returning without it
    // can cut a cold start short and log a false failure.
    ctx.waitUntil(ping(event.cron))
  },
}
