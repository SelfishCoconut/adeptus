import { useNavigate } from 'react-router-dom'
import { useLogout, useMe } from '@/features/auth/api'
import { TermsGate } from '@/features/auth/components/TermsGate'
import { WorkspaceShell } from '@/features/workspace/WorkspaceShell'

export function WorkspacePage() {
  const navigate = useNavigate()
  const me = useMe()
  const logout = useLogout()

  // ProtectedRoute already ensures a user is present; this narrows the type.
  if (!me.data) {
    return null
  }

  function handleLogout() {
    logout.mutate(undefined, {
      // useLogout clears the entire query cache on success.
      onSuccess: () => navigate('/login'),
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
