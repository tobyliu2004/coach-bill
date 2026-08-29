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
const History = lazy(() => import('./pages/History'))
const Trends = lazy(() => import('./pages/Trends'))
// Dev-only artboards for og.png / apple-touch-icon.png — see
// frontend/src/dev/OgCard.tsx. The DEV check has to wrap the lazy() call, not
// just the <Route>: guarding the route alone still emitted an OgCard chunk and
// still referenced it from the entry (verified in dist/). Inside a ternary the
// whole branch is dead code in the prod build, so the import disappears.
const OgCard = import.meta.env.DEV ? lazy(() => import('./dev/OgCard')) : null

export function AppRoutes() {
  return (
    <Suspense fallback={null}>
      <Routes>
        <Route path="/" element={<Landing />} />
        {OgCard && <Route path="/__og" element={<OgCard />} />}
        <Route element={<AuthLayout />}>
          <Route path="/login" element={<Login />} />
          <Route path="/auth/callback" element={<AuthCallback />} />
          <Route path="/auth/reset" element={<ResetPassword />} />
          <Route element={<ProtectedRoute />}>
            <Route path="/onboarding" element={<Onboarding />} />
            <Route path="/app" element={<AppHome />} />
            <Route path="/history" element={<History />} />
            <Route path="/trends" element={<Trends />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  )
}
