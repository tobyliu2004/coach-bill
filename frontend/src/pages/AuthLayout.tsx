import { Outlet } from 'react-router'

/**
 * Shared shell for every non-landing route (/login, /auth/*, /onboarding, /app).
 *
 * This is a lazy chunk boundary: everything auth-related — including supabase-js once it
 * arrives — loads from here, so a visitor who only reads the landing page never downloads
 * any of it. AuthProvider mounts here (not at the root) for the same reason.
 */
function AuthLayout() {
  return (
    <>
      <div aria-hidden className="grain" />
      <Outlet />
    </>
  )
}

export default AuthLayout
