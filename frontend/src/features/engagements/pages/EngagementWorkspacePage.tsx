import { useParams } from 'react-router-dom'
import { useNavigate } from 'react-router-dom'
import { useLogout, useMe } from '@/features/auth/api'
import { TermsGate } from '@/features/auth/components/TermsGate'
import { WorkspaceShell } from '@/features/workspace/WorkspaceShell'

export function EngagementWorkspacePage() {
  // Engagement id is available for downstream components (future slices).
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const { id: _engagementId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const me = useMe()
  const logout = useLogout()

  // ProtectedRoute already ensures a user is present; this narrows the type.
  if (!me.data) {
    return null
  }

  function handleLogout() {
    logout.mutate(undefined, {
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
