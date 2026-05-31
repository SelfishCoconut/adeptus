import { useState, type FormEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useAddMember } from '../api'

interface InviteMemberFormProps {
  engagementId: string
  /** The caller's role in this engagement — form is hidden for non-owners. */
  callerRole: 'owner' | 'member'
}

export function InviteMemberForm({ engagementId, callerRole }: InviteMemberFormProps) {
  const addMember = useAddMember(engagementId)
  const [username, setUsername] = useState('')

  if (callerRole !== 'owner') {
    return null
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!username.trim()) return
    addMember.mutate(
      { username: username.trim() },
      {
        onSuccess: () => {
          setUsername('')
        },
      },
    )
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3">
      <div className="flex flex-col gap-2">
        <Label htmlFor="invite-username">Invite member</Label>
        <div className="flex gap-2">
          <Input
            id="invite-username"
            name="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="Username"
            disabled={addMember.isPending}
            aria-invalid={addMember.isError}
          />
          <Button type="submit" disabled={addMember.isPending || !username.trim()}>
            {addMember.isPending ? 'Inviting…' : 'Invite'}
          </Button>
        </div>
        {addMember.isError && (
          <p role="alert" className="text-sm text-destructive">
            {addMember.error instanceof Error
              ? addMember.error.message
              : 'Failed to invite member.'}
          </p>
        )}
      </div>
    </form>
  )
}
