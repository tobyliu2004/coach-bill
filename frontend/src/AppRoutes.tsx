import { Suspense, lazy } from 'react'
import { Navigate, Route, Routes } from 'react-router'
import Landing from './pages/Landing'

// Everything behind login is a separate chunk — landing visitors never download it.
const AuthLayout = lazy(() => import('./pages/AuthLayout'))
const Login = lazy(() => import('./pages/Login'))
const AppHome = lazy(() => import('./pages/AppHome'))

export function AppRoutes() {
  return (
    <Suspense fallback={null}>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route element={<AuthLayout />}>
          <Route path="/login" element={<Login />} />
          <Route path="/app" element={<AppHome />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  )
}
