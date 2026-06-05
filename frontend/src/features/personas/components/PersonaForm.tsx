import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'

interface PersonaFormProps {
  /** Pre-fill for edit mode; empty for create. */
  initialName?: string
  initialSystemPrompt?: string
  submitLabel: string
  submitting: boolean
  /** Inline error (e.g. the name-conflict 409); rendered above the buttons. */
  errorMessage: string | null
  onSubmit: (values: { name: string; systemPrompt: string }) => void
  onCancel: () => void
}

/**
 * Create/edit form for a custom persona: a name + a system prompt, both sent verbatim
 * (§5.5). Submit is disabled until both are non-empty; the parent wires the create/update
 * mutation and feeds back a name-conflict (409) as `errorMessage`.
 */
export function PersonaForm({
  initialName = '',
  initialSystemPrompt = '',
  submitLabel,
  submitting,
  errorMessage,
  onSubmit,
  onCancel,
}: PersonaFormProps) {
  const [name, setName] = useState(initialName)
  const [systemPrompt, setSystemPrompt] = useState(initialSystemPrompt)

  const canSubmit = name.trim().length > 0 && systemPrompt.trim().length > 0 && !submitting

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault()
        if (!canSubmit) return
        onSubmit({ name: name.trim(), systemPrompt: systemPrompt.trim() })
      }}
    >
      <div className="flex flex-col gap-1">
        <Label htmlFor="persona-name">Name</Label>
        <Input
          id="persona-name"
          value={name}
          maxLength={80}
          onChange={(event) => setName(event.target.value)}
          placeholder="e.g. Cloud Pentest"
        />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="persona-system-prompt">System prompt</Label>
        <Textarea
          id="persona-system-prompt"
          value={systemPrompt}
          rows={6}
          onChange={(event) => setSystemPrompt(event.target.value)}
          placeholder="How should the AI behave for this persona?"
        />
      </div>
      {errorMessage ? (
        <p role="alert" className="text-xs text-destructive">
          {errorMessage}
        </p>
      ) : null}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={!canSubmit}>
          {submitLabel}
        </Button>
      </div>
    </form>
  )
}
