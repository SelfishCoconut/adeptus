import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import type { MemberEntry } from '@/shared/api'
import { useMembers, useRemoveMember } from '../api'

interface MembersListProps {
  engagementId: string
  /** The caller's role in this engagement — controls owner-only UI. */
  callerRole: 'owner' | 'member'
}

export function MembersList({ engagementId, callerRole }: MembersListProps) {
  const { data, isLoading, isError, error } = useMembers(engagementId)
  const removeMember = useRemoveMember(engagementId)

  if (isLoading) {
    return (
      <div data-testid="members-list-skeleton" className="flex flex-col gap-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    )
  }

  if (isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {error instanceof Error ? error.message : 'Failed to load members.'}
      </p>
    )
  }

  if (!data || data.length === 0) {
    return <p className="text-sm text-muted-foreground">No members yet.</p>
  }

  return (
    <ul className="flex flex-col gap-2">
      {data.map((m: MemberEntry) => (
        <li
          key={m.user_id}
          className="flex items-center justify-between rounded-md border px-3 py-2"
        >
          <span className="text-sm font-medium">{m.username}</span>
          <div className="flex items-center gap-2">
            <Badge variant={m.role === 'owner' ? 'default' : 'secondary'}>{m.role}</Badge>
            {callerRole === 'owner' && m.role !== 'owner' && (
              <Button
                variant="destructive"
                size="sm"
                disabled={removeMember.isPending}
                onClick={() => removeMember.mutate(m.user_id)}
              >
                Remove
              </Button>
            )}
          </div>
        </li>
      ))}
    </ul>
  )
}
