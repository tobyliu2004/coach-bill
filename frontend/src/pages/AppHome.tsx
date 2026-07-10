import { useAuth } from '../auth/useAuth'

/**
 * The daily app shell. Deliberately quiet — no Lenis, no signature moments; those are
 * spent on marketing. This screen optimizes for speed and will hold the check-in flow.
 */
function AppHome() {
  const { profile, session, signOut } = useAuth()
  // ProtectedRoute only renders this once the profile is loaded.
  const name = profile?.display_name ?? session?.user.email ?? 'you'

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="border-b border-edge">
        <div className="mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-4">
          <span className="font-display text-lg font-semibold tracking-tight text-fg">
            Coach Bill
          </span>
          <div className="flex items-center gap-4">
            <span className="font-mono text-xs text-fg-muted">{name}</span>
            <button
              type="button"
              onClick={() => void signOut()}
              className="rounded-control border border-edge-strong px-3 py-1.5 text-xs font-semibold text-fg transition-colors duration-150 hover:border-fg-muted"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col items-start justify-center gap-3 px-6 py-16">
        {profile?.goal && (
          <p className="font-mono text-xs tracking-wider text-fg-muted uppercase">
            Goal — <span className="text-fg normal-case">{profile.goal}</span>
          </p>
        )}
        <h1 className="font-display text-display-sm text-fg">No check-ins yet.</h1>
        <p className="max-w-md text-base leading-relaxed text-fg-muted">
          Text check-ins land next — type &ldquo;bench 135 4×8, slept 7h&rdquo; and Bill
          logs every set.
        </p>
      </main>
    </div>
  )
}

export default AppHome
