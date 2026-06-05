import { useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { Persona } from '@/shared/api'
import {
  PersonaNameConflictError,
  useCreatePersona,
  useDeletePersona,
  usePersonas,
  useUpdatePersona,
} from '../api'
import { PersonaForm } from './PersonaForm'

interface ManagePersonasPanelProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

type Mode = { kind: 'list' } | { kind: 'create' } | { kind: 'edit'; persona: Persona }

const NAME_CONFLICT_MESSAGE = 'You already have a persona with this name.'

/**
 * The "Manage personas" panel (§5.3): lists the built-ins (read-only, badged) and the
 * caller's own custom personas (Edit/Delete), and hosts the create/edit form. Built-ins
 * are never editable or deletable here — they have no action buttons — mirroring the
 * server's 404 on a built-in edit/delete (§17.1 / Risk 2). Delete is two-step (confirm).
 */
export function ManagePersonasPanel({ open, onOpenChange }: ManagePersonasPanelProps) {
  const personasQuery = usePersonas({ enabled: open })
  const createMutation = useCreatePersona()
  const updateMutation = useUpdatePersona()
  const deleteMutation = useDeletePersona()

  const [mode, setMode] = useState<Mode>({ kind: 'list' })
  const [formError, setFormError] = useState<string | null>(null)
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)

  const personas = personasQuery.data?.items ?? []
  const builtins = personas.filter((p) => p.is_builtin)
  const custom = personas.filter((p) => !p.is_builtin)

  const backToList = () => {
    setMode({ kind: 'list' })
    setFormError(null)
  }

  const handleMutationError = (error: unknown) => {
    setFormError(
      error instanceof PersonaNameConflictError
        ? NAME_CONFLICT_MESSAGE
        : 'Something went wrong — please try again.',
    )
  }

  const submitCreate = (values: { name: string; systemPrompt: string }) => {
    setFormError(null)
    createMutation.mutate(values, { onSuccess: backToList, onError: handleMutationError })
  }

  const submitEdit = (id: string, values: { name: string; systemPrompt: string }) => {
    setFormError(null)
    updateMutation.mutate(
      { id, name: values.name, systemPrompt: values.systemPrompt },
      { onSuccess: backToList, onError: handleMutationError },
    )
  }

  const confirmDelete = (id: string) => {
    deleteMutation.mutate(id, { onSettled: () => setConfirmingDeleteId(null) })
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) backToList()
        onOpenChange(next)
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Manage personas</DialogTitle>
          <DialogDescription>
            Built-in personas are shared and read-only. Create your own to tailor the AI&apos;s
            system prompt; your personas are private to you.
          </DialogDescription>
        </DialogHeader>

        {mode.kind === 'create' ? (
          <PersonaForm
            submitLabel="Create persona"
            submitting={createMutation.isPending}
            errorMessage={formError}
            onSubmit={submitCreate}
            onCancel={backToList}
          />
        ) : mode.kind === 'edit' ? (
          <PersonaForm
            initialName={mode.persona.name}
            initialSystemPrompt={mode.persona.system_prompt}
            submitLabel="Save changes"
            submitting={updateMutation.isPending}
            errorMessage={formError}
            onSubmit={(values) => submitEdit(mode.persona.id, values)}
            onCancel={backToList}
          />
        ) : (
          <div className="flex flex-col gap-3">
            <ul className="flex flex-col gap-2">
              {builtins.map((persona) => (
                <li
                  key={persona.id}
                  className="flex items-center justify-between rounded-md border px-3 py-2"
                >
                  <span className="text-sm">{persona.name}</span>
                  <Badge variant="secondary">Built-in</Badge>
                </li>
              ))}
              {custom.map((persona) => (
                <li
                  key={persona.id}
                  className="flex items-center justify-between rounded-md border px-3 py-2"
                >
                  <span className="text-sm">{persona.name}</span>
                  {confirmingDeleteId === persona.id ? (
                    <span className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground">Delete?</span>
                      <Button
                        type="button"
                        variant="destructive"
                        size="sm"
                        onClick={() => confirmDelete(persona.id)}
                      >
                        Confirm
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => setConfirmingDeleteId(null)}
                      >
                        Cancel
                      </Button>
                    </span>
                  ) : (
                    <span className="flex items-center gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          setFormError(null)
                          setMode({ kind: 'edit', persona })
                        }}
                      >
                        Edit
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => setConfirmingDeleteId(persona.id)}
                      >
                        Delete
                      </Button>
                    </span>
                  )}
                </li>
              ))}
            </ul>
            <div className="flex justify-end">
              <Button
                type="button"
                onClick={() => {
                  setFormError(null)
                  setMode({ kind: 'create' })
                }}
              >
                New persona
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
