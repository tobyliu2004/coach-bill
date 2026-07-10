import { Outlet } from 'react-router'
import { AuthProvider } from '../auth/AuthProvider'

/**
 * Shared shell for every non-landing route (/login, /auth/*, /onboarding, /app).
 *
 * This is a lazy chunk boundary: everything auth-related — supabase-js included — loads
 * from here, so a visitor who only reads the landing page never downloads any of it.
 * AuthProvider mounts here (not at the root) for the same reason.
 */
function AuthLayout() {
  return (
    <AuthProvider>
      <div aria-hidden className="grain" />
      <Outlet />
    </AuthProvider>
  )
}

export default AuthLayout
