import { useNavigate } from 'react-router-dom'
import { useLogout, useMe } from '@/features/auth/api'
import { useAuthStore } from '@/features/auth/store'
import { TermsGate } from '@/features/auth/components/TermsGate'
import { WorkspaceShell } from '@/features/workspace/WorkspaceShell'

export function WorkspacePage() {
  const navigate = useNavigate()
  const me = useMe()
  const logout = useLogout()
  const setUser = useAuthStore((state) => state.setUser)

  // ProtectedRoute already ensures a user is present; this narrows the type.
  if (!me.data) {
    return null
  }

  function handleLogout() {
    logout.mutate(undefined, {
      onSuccess: () => {
        setUser(null)
        navigate('/login')
      },
    })
  }

  return (
    <TermsGate>
      <WorkspaceShell
        username={me.data.username}
        role={me.data.role}
        onLogout={handleLogout}
        isLoggingOut={logout.isPending}
      />
    </TermsGate>
  )
}
