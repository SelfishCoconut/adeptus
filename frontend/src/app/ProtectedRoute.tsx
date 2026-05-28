import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useMe } from '@/features/auth/api'

// Session guard. useMe resolves a 401 to null, so "no data" means
// unauthenticated (or expired) and we silently redirect to /login.
export function ProtectedRoute({ children }: { children: ReactNode }) {
  const me = useMe()

  if (me.isPending) {
    return null
  }
  if (!me.data) {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}
