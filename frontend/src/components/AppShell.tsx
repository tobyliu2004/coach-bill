import type { ReactNode } from 'react'
import { NavLink } from 'react-router'
import { useAuth } from '../auth/useAuth'

/**
 * The chrome every signed-in screen wears: wordmark, the nav between screens, who you are,
 * and the way out.
 *
 * Extracted from AppHome the moment a second screen existed, rather than copied — a header
 * duplicated across pages is how the sign-out button ends up in two places and drifts. Two
 * screens use this today, three after Trends.
 *
 * The nav stays grayscale on purpose: active is `text-fg`, inactive is `text-fg-muted`.
 * Amber is capped at ~5% of a screen and is already spent on the primary CTA (design.md);
 * an accented nav item would compete with "Log check-in" for the one moment of energy.
 */

const navLinkClasses = 'text-sm transition-colors duration-150'

function AppNavLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `${navLinkClasses} ${isActive ? 'text-fg' : 'text-fg-muted hover:text-fg'}`
      }
    >
      {children}
    </NavLink>
  )
}

export function AppShell({ children }: { children: ReactNode }) {
  const { profile, session, signOut } = useAuth()
  // ProtectedRoute only renders these screens once the profile is loaded.
  const name = profile?.display_name ?? session?.user.email ?? 'you'

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="border-b border-edge">
        <div className="mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-8">
            <span className="font-display text-lg font-semibold tracking-tight text-fg">
              Coach Bill
            </span>
            <nav className="flex items-center gap-5">
              <AppNavLink to="/app">Today</AppNavLink>
              <AppNavLink to="/history">History</AppNavLink>
            </nav>
          </div>
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

      {children}
    </div>
  )
}
