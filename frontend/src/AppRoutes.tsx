import { Suspense, lazy } from 'react'
import { Navigate, Route, Routes } from 'react-router'
import Landing from './pages/Landing'

// Everything behind login is a separate chunk — landing visitors never download it.
// ProtectedRoute must stay lazy too: a static import would chain supabase-js into
// the landing bundle via AuthProvider.
const AuthLayout = lazy(() => import('./pages/AuthLayout'))
const ProtectedRoute = lazy(() =>
  import('./auth/ProtectedRoute').then((m) => ({ default: m.ProtectedRoute })),
)
const AuthCallback = lazy(() => import('./pages/AuthCallback'))
const Onboarding = lazy(() => import('./pages/Onboarding'))
const ResetPassword = lazy(() => import('./pages/ResetPassword'))
const Login = lazy(() => import('./pages/Login'))
const AppHome = lazy(() => import('./pages/AppHome'))

export function AppRoutes() {
  return (
    <Suspense fallback={null}>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route element={<AuthLayout />}>
          <Route path="/login" element={<Login />} />
          <Route path="/auth/callback" element={<AuthCallback />} />
          <Route path="/auth/reset" element={<ResetPassword />} />
          <Route element={<ProtectedRoute />}>
            <Route path="/onboarding" element={<Onboarding />} />
            <Route path="/app" element={<AppHome />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  )
}
