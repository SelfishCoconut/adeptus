// Controlled dialog: accepts open/onOpenChange from the parent (e.g. EngagementsPage).
// This makes the component testable by passing open={true} and lets the parent
// own the trigger button, keeping the dialog focused on form logic only.
import { useState, type FormEvent } from 'react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Switch } from '@/components/ui/switch'
import { useCreateEngagement } from '../api'
import type { EngagementCreate } from '@/shared/api'

interface FieldErrors {
  name?: string
  scope?: string
  client_info?: string
}

// Narrows an unknown error value to a map of field → message from a FastAPI
// HTTPValidationError detail array.  The field name is the last element of
// each error's `loc` array (e.g. ["body","name"] → "name").
function extractFieldErrors(err: unknown): FieldErrors {
  if (!err || typeof err !== 'object') return {}
  const detail = (err as Record<string, unknown>)['detail']
  if (!Array.isArray(detail)) return {}
  const map: FieldErrors = {}
  for (const item of detail) {
    if (!item || typeof item !== 'object') continue
    const loc = (item as Record<string, unknown>)['loc']
    const msg = (item as Record<string, unknown>)['msg']
    if (!Array.isArray(loc) || typeof msg !== 'string') continue
    const field = loc[loc.length - 1]
    if (field === 'name') map.name = msg
    else if (field === 'scope') map.scope = msg
    else if (field === 'client_info') map.client_info = msg
  }
  return map
}

interface NewEngagementDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function NewEngagementDialog({ open, onOpenChange }: NewEngagementDialogProps) {
  const createEngagement = useCreateEngagement()

  const [name, setName] = useState('')
  const [scope, setScope] = useState('')
  const [clientInfo, setClientInfo] = useState('')
  const [cloudEnabled, setCloudEnabled] = useState(false)

  const fieldErrors = extractFieldErrors(createEngagement.error)

  function resetFields() {
    setName('')
    setScope('')
    setClientInfo('')
    setCloudEnabled(false)
    createEngagement.reset()
  }

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen) {
      resetFields()
    }
    onOpenChange(nextOpen)
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const body: EngagementCreate = {
      name,
      scope,
      client_info: clientInfo || null,
      privacy_mode: cloudEnabled ? 'cloud_enabled' : 'local_only',
    }
    createEngagement.mutate(body, {
      onSuccess: () => {
        resetFields()
        onOpenChange(false)
      },
    })
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New Engagement</DialogTitle>
          <DialogDescription>
            Fill in the details below to create a new engagement. You will automatically become
            the owner.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
          <div className="flex flex-col gap-2">
            <Label htmlFor="engagement-name">Name</Label>
            <Input
              id="engagement-name"
              name="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              disabled={createEngagement.isPending}
              aria-invalid={!!fieldErrors.name}
            />
            {fieldErrors.name && (
              <p role="alert" className="text-sm text-destructive">
                {fieldErrors.name}
              </p>
            )}
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="engagement-scope">Scope</Label>
            <Textarea
              id="engagement-scope"
              name="scope"
              value={scope}
              onChange={(e) => setScope(e.target.value)}
              required
              disabled={createEngagement.isPending}
              aria-invalid={!!fieldErrors.scope}
              placeholder="IPs, CIDR ranges, domains — one per line"
            />
            {fieldErrors.scope && (
              <p role="alert" className="text-sm text-destructive">
                {fieldErrors.scope}
              </p>
            )}
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="engagement-client-info">Client Info (optional)</Label>
            <Textarea
              id="engagement-client-info"
              name="client_info"
              value={clientInfo}
              onChange={(e) => setClientInfo(e.target.value)}
              disabled={createEngagement.isPending}
              aria-invalid={!!fieldErrors.client_info}
              placeholder="Contact details, context, notes…"
            />
            {fieldErrors.client_info && (
              <p role="alert" className="text-sm text-destructive">
                {fieldErrors.client_info}
              </p>
            )}
          </div>

          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-3">
              <Switch
                id="engagement-cloud-llm"
                checked={cloudEnabled}
                onCheckedChange={setCloudEnabled}
                disabled={createEngagement.isPending}
              />
              <Label htmlFor="engagement-cloud-llm">Cloud LLM enabled</Label>
            </div>
            <p className="text-sm text-muted-foreground">
              Allow Claude API calls for this engagement. Off by default (strict local-only).
            </p>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => handleOpenChange(false)}
              disabled={createEngagement.isPending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={createEngagement.isPending}>
              {createEngagement.isPending ? 'Creating…' : 'Create'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
