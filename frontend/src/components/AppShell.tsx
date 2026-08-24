import type { ReactNode } from 'react'
import { NavLink } from 'react-router'
import { useAuth } from '../auth/useAuth'

/**
 * The chrome every signed-in screen wears: wordmark, the nav between screens, who you are,
 * and the way out.
 *
 * Extracted from AppHome the moment a second screen existed, rather than copied — a header
 * duplicated across pages is how the sign-out button ends up in two places and drifts. All
 * five screens use it.
 *
 * The nav stays grayscale on purpose: active is `text-fg`, inactive is `text-fg-muted`.
 * Amber is capped at ~5% of a screen and is already spent on the primary CTA (design.md);
 * an accented nav item would compete with "Log check-in" for the one moment of energy.
 *
 * ⚠️ RESPONSIVE, AND #51 ROW 25 IS WHY. Until now this was ONE FLEX ROW with no variant at
 * any breakpoint — wordmark + 3 links + email + Sign out — so on a phone it overflowed the
 * viewport and the last items were simply unreachable. That is not a cosmetic bug: it is
 * the concrete reason /history and /trends were live on coachbill.fit for weeks and Toby
 * had never seen them. #51 makes it five links, which would have made it worse.
 *
 * HOW IT REFLOWS, and why it is `flex-wrap` + `order` rather than a mobile/desktop pair:
 * duplicating the header for two breakpoints is how the sign-out button ends up in two
 * places and drifts — the exact thing this component was extracted to prevent. So there is
 * ONE of everything, and the layout reorders instead.
 *   narrow : line 1 is the wordmark and the identity block (`mr-auto` pushes them apart);
 *            the nav wraps to line 2 at full width and scrolls horizontally if five labels
 *            still do not fit a 320px screen.
 *   sm+    : one row again — wordmark, nav, then the identity block pushed right.
 * `overflow-x-auto` is the floor under the whole thing: whatever the label lengths, the
 * page body can never scroll sideways.
 */

const navLinkClasses = 'whitespace-nowrap text-sm transition-colors duration-150'

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
        <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3 sm:flex-nowrap sm:gap-x-8 sm:px-6 sm:py-4">
          <span className="mr-auto font-display text-lg font-semibold tracking-tight text-fg sm:mr-0">
            Coach Bill
          </span>

          {/* Last on a narrow screen (its own full-width line), second on a wide one. The
              negative margin lets the scrolled edges bleed to the viewport instead of
              stopping short of the padding, so a cut-off label reads as scrollable. */}
          <nav className="order-last -mx-4 w-full overflow-x-auto px-4 sm:order-none sm:mx-0 sm:w-auto sm:overflow-visible sm:px-0">
            <div className="flex items-center gap-5">
              <AppNavLink to="/app">Today</AppNavLink>
              <AppNavLink to="/plan">Plan</AppNavLink>
              <AppNavLink to="/diet">Diet</AppNavLink>
              <AppNavLink to="/history">History</AppNavLink>
              <AppNavLink to="/trends">Trends</AppNavLink>
            </div>
          </nav>

          <div className="flex items-center gap-4 sm:ml-auto">
            {/* Truncated rather than hidden: on a phone this is the only confirmation of
                WHICH account you are looking at, and a long email must not push Sign out
                off the edge — the failure this whole change is about. */}
            <span className="max-w-[10rem] truncate font-mono text-xs text-fg-muted sm:max-w-none">
              {name}
            </span>
            <button
              type="button"
              onClick={() => void signOut()}
              className="whitespace-nowrap rounded-control border border-edge-strong px-3 py-1.5 text-xs font-semibold text-fg transition-colors duration-150 hover:border-fg-muted"
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
