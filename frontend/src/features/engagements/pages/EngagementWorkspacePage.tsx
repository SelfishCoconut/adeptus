import { useParams } from 'react-router-dom'
import { useNavigate } from 'react-router-dom'
import { useLogout, useMe } from '@/features/auth/api'
import { TermsGate } from '@/features/auth/components/TermsGate'
import { WorkspaceShell } from '@/features/workspace/WorkspaceShell'
import { useEngagement } from '../api'
import { MembersList } from '../components/MembersList'
import { InviteMemberForm } from '../components/InviteMemberForm'

export function EngagementWorkspacePage() {
  const { id: engagementId = '' } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const me = useMe()
  const logout = useLogout()
  const engagement = useEngagement(engagementId)

  // ProtectedRoute already ensures a user is present; this narrows the type.
  if (!me.data) {
    return null
  }

  function handleLogout() {
    logout.mutate(undefined, {
      onSuccess: () => navigate('/login'),
    })
  }

  // Caller's role defaults to 'member' while the engagement query is loading.
  const callerRole = engagement.data?.member_role ?? 'member'

  return (
    <TermsGate>
      <WorkspaceShell
        username={me.data.username}
        role={me.data.role}
        onLogout={handleLogout}
        isLoggingOut={logout.isPending}
      />
      {engagementId && (
        <section aria-label="Membership" className="border-t px-6 py-6">
          <h2 className="mb-4 text-lg font-semibold">Members</h2>
          <InviteMemberForm engagementId={engagementId} callerRole={callerRole} />
          <div className="mt-4">
            <MembersList engagementId={engagementId} callerRole={callerRole} />
          </div>
        </section>
      )}
    </TermsGate>
  )
}
