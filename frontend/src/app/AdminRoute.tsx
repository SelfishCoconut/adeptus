import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useMe } from '@/features/auth/api'

// Role guard layered on top of auth. ProtectedRoute already ensures a session
// exists; AdminRoute additionally requires role === 'admin'. Any authenticated
// non-admin is redirected to /engagements (the default authed landing page).
export function AdminRoute({ children }: { children: ReactNode }) {
  const me = useMe()

  if (me.isPending) {
    return null
  }
  if (!me.data) {
    return <Navigate to="/login" replace />
  }
  if (me.data.role !== 'admin') {
    return <Navigate to="/engagements" replace />
  }
  return <>{children}</>
}
