import { useEffect, useState } from 'react'

/** Mirror of the backend's HealthStatus response (see backend/app/schemas/health.py). */
interface HealthStatus {
  ok: boolean
  db: 'up' | 'down'
  latency_ms: number
}

type FetchState =
  | { kind: 'loading' }
  | { kind: 'ok'; data: HealthStatus }
  | { kind: 'error'; message: string }

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

/** The old status page, demoted to a footer dot: green = API + DB reachable. */
export function HealthDot() {
  const [state, setState] = useState<FetchState>({ kind: 'loading' })

  useEffect(() => {
    let cancelled = false
    fetch(`${API_URL}/health/db`)
      .then(async (res) => {
        // Both 200 (up) and 503 (down) carry a HealthStatus body by design.
        const data = (await res.json()) as HealthStatus
        if (typeof data.ok !== 'boolean') throw new Error(`unexpected response (HTTP ${res.status})`)
        if (!cancelled) setState({ kind: 'ok', data })
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'request failed'
        if (!cancelled) setState({ kind: 'error', message })
      })
    return () => {
      cancelled = true
    }
  }, [])

  const up = state.kind === 'ok' && state.data.ok
  const label =
    state.kind === 'loading'
      ? 'checking systems…'
      : up
        ? `systems up · ${state.kind === 'ok' ? state.data.latency_ms : 0} ms`
        : 'systems down'

  return (
    <span className="inline-flex items-center gap-2 font-mono text-xs tabular-nums text-fg-muted">
      <span
        aria-hidden
        className={`size-1.5 rounded-full ${
          state.kind === 'loading' ? 'bg-fg-muted' : up ? 'bg-accent' : 'bg-accent-hot'
        }`}
      />
      {label}
    </span>
  )
}
