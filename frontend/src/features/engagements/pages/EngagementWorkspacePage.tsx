import { useCallback, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useLogout, useMe } from '@/features/auth/api'
import { TermsGate } from '@/features/auth/components/TermsGate'
import { WorkspaceShell } from '@/features/workspace/WorkspaceShell'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
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

  // Controlled slot-limit input with a "draft" approach:
  // - `slotLimitDraft` holds the user's in-progress edit (null when no edit is in flight).
  // - When no draft exists, the live server value is shown.
  // - On blur/Enter the draft is committed (mutation fired) and cleared so the input
  //   reflects the updated server value after the next refetch.
  // This avoids the setState-in-effect pattern (react-hooks/set-state-in-effect lint rule)
  // because the server value is read directly from the query, not mirrored through state.
  const [slotLimitDraft, setSlotLimitDraft] = useState<number | null>(null)
  const displayedSlotLimit =
    slotLimitDraft !== null ? slotLimitDraft : (engagement.data?.concurrency_slot_limit ?? 3)

  // All hooks must be called before any conditional return (Rules of Hooks).
  const handlePrivacyToggle = useCallback(
    (checked: boolean) => {
      updateEngagement.mutate({ privacy_mode: checked ? 'cloud_enabled' : 'local_only' })
    },
    [updateEngagement],
  )

  // Commit on blur (or Enter): validate range 1–16 then fire exactly one mutation.
  // This mirrors handlePrivacyToggle (fires once per user decision, not per keystroke).
  const handleSlotLimitCommit = useCallback(() => {
    const value = slotLimitDraft ?? (engagement.data?.concurrency_slot_limit ?? 3)
    if (!Number.isInteger(value) || value < 1 || value > 16) {
      setSlotLimitDraft(null) // Reset invalid draft to server value.
      return
    }
    updateEngagement.mutate({ concurrency_slot_limit: value })
    setSlotLimitDraft(null) // Clear draft so server value shows after refetch.
  }, [slotLimitDraft, engagement.data?.concurrency_slot_limit, updateEngagement])

  const handleSlotLimitKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') {
        handleSlotLimitCommit()
      }
    },
    [handleSlotLimitCommit],
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
        engagementId={engagementId}
      />
      {engagementId && callerRole === 'owner' && (
        <div className="flex flex-wrap items-center gap-6 border-b px-6 py-3">
          <div className="flex items-center gap-2">
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
          <div className="flex items-center gap-2">
            <Label htmlFor="concurrency-slot-limit" className="text-sm whitespace-nowrap">
              Concurrent tool slots
            </Label>
            <Input
              id="concurrency-slot-limit"
              type="number"
              min={1}
              max={16}
              value={displayedSlotLimit}
              disabled={updateEngagement.isPending}
              onChange={(e) => setSlotLimitDraft(e.target.valueAsNumber)}
              onBlur={handleSlotLimitCommit}
              onKeyDown={handleSlotLimitKeyDown}
              className="w-20"
              aria-label="Concurrent tool slots"
            />
          </div>
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
