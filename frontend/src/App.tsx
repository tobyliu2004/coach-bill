import { useEffect, useState } from 'react'

/** Mirror of the backend's HealthStatus response (see backend/app/schemas/health.py). */
interface HealthStatus {
  ok: boolean
  db: string
  latency_ms: number
}

/** The fetch is always in exactly one of these states — the union makes each branch type-safe. */
type FetchState =
  | { kind: 'loading' }
  | { kind: 'ok'; data: HealthStatus }
  | { kind: 'error'; message: string }

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

function App() {
  const [state, setState] = useState<FetchState>({ kind: 'loading' })

  useEffect(() => {
    let cancelled = false
    fetch(`${API_URL}/health/db`)
      .then(async (res) => {
        const data = (await res.json()) as HealthStatus
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

  return (
    <main style={{ fontFamily: 'system-ui, sans-serif', padding: '3rem', textAlign: 'center' }}>
      <h1>Coach Bill</h1>
      <p style={{ color: '#666' }}>Backend / database health</p>
      <StatusBadge state={state} />
    </main>
  )
}

function StatusBadge({ state }: { state: FetchState }) {
  switch (state.kind) {
    case 'loading':
      return <Badge color="#888" label="checking…" />
    case 'error':
      return <Badge color="#c0392b" label={`unreachable — ${state.message}`} />
    case 'ok':
      return state.data.ok ? (
        <Badge color="#27ae60" label={`DB up · ${state.data.latency_ms} ms`} />
      ) : (
        <Badge color="#c0392b" label={`DB down (${state.data.db})`} />
      )
  }
}

function Badge({ color, label }: { color: string; label: string }) {
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '0.5rem 1.25rem',
        borderRadius: 999,
        background: color,
        color: 'white',
        fontWeight: 600,
      }}
    >
      {label}
    </span>
  )
}

export default App
