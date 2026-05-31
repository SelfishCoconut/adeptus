import { useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useLogout, useMe } from '@/features/auth/api'
import { TermsGate } from '@/features/auth/components/TermsGate'
import { WorkspaceShell } from '@/features/workspace/WorkspaceShell'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { useEngagement, useUpdateEngagement } from '../api'
import { MembersList } from '../components/MembersList'
import { InviteMemberForm } from '../components/InviteMemberForm'

export function EngagementWorkspacePage() {
  const { id: engagementId = '' } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const me = useMe()
  const logout = useLogout()
  const engagement = useEngagement(engagementId)
  const updateEngagement = useUpdateEngagement(engagementId)

  // All hooks must be called before any conditional return (Rules of Hooks).
  const handlePrivacyToggle = useCallback(
    (checked: boolean) => {
      updateEngagement.mutate({ privacy_mode: checked ? 'cloud_enabled' : 'local_only' })
    },
    [updateEngagement],
  )

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

  // Safe default per §17.5: show local_only while loading.
  const privacyMode = engagement.data?.privacy_mode ?? 'local_only'
  const isCloudEnabled = privacyMode === 'cloud_enabled'

  return (
    <TermsGate>
      <WorkspaceShell
        username={me.data.username}
        role={me.data.role}
        onLogout={handleLogout}
        isLoggingOut={logout.isPending}
        privacyMode={privacyMode}
      />
      {engagementId && callerRole === 'owner' && (
        <div className="flex items-center gap-2 border-b px-6 py-3">
          <Switch
            id="cloud-llm-toggle"
            checked={isCloudEnabled}
            onCheckedChange={handlePrivacyToggle}
            disabled={updateEngagement.isPending}
          />
          <Label htmlFor="cloud-llm-toggle" className="cursor-pointer text-sm">
            Enable cloud LLM
          </Label>
        </div>
      )}
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
