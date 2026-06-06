// FindingDialog — create or edit a finding. When `finding` is provided the dialog
// is in edit mode (pre-fills fields, calls useUpdateFinding); when absent it is in
// create mode (calls useCreateFinding). The optional node link is a picker fed by
// the existing graph snapshot (useGraph) with a "None" option that unlinks.
//
// The inner <FindingForm> is mounted only while the dialog is open and is keyed so
// it remounts (fresh lazy-seeded state) each time the dialog opens or the target
// finding changes — no useEffect reset needed (mirrors NodeEditDialog).
import { useState, type FormEvent } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useGraph } from '@/features/graph/api'
import { useCreateFinding, useUpdateFinding } from '../api'
import type { Finding, Severity } from '../api'
import { SEVERITY_LABELS, SEVERITY_ORDER } from '../findingsLabels'

const SELECT_CLASS =
  'h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs ' +
  'outline-none transition-[color,box-shadow] focus-visible:border-ring focus-visible:ring-[3px] ' +
  'focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed ' +
  'disabled:opacity-50'

export interface FindingDialogProps {
  engagementId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  /** When provided, the dialog is in edit mode and pre-fills these values. */
  finding?: Finding
}

interface FindingFormProps {
  engagementId: string
  onOpenChange: (open: boolean) => void
  finding?: Finding
}

function FindingForm({ engagementId, onOpenChange, finding }: FindingFormProps) {
  const isEditMode = finding !== undefined
  const createFinding = useCreateFinding(engagementId)
  const updateFinding = useUpdateFinding(engagementId)
  const mutation = isEditMode ? updateFinding : createFinding
  const graph = useGraph(engagementId)

  // Lazy initial state seeded from props — no useEffect needed.
  const [title, setTitle] = useState(() => finding?.title ?? '')
  const [severity, setSeverity] = useState<Severity>(() => finding?.severity ?? 'medium')
  const [description, setDescription] = useState(() => finding?.description ?? '')
  // "" represents "no link"; otherwise a node id.
  const [nodeId, setNodeId] = useState(() => finding?.node_id ?? '')

  const nodes = graph.data?.nodes ?? []

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const linkedNode = nodeId === '' ? null : nodeId

    if (isEditMode && finding) {
      updateFinding.mutate(
        { findingId: finding.id, title, severity, description, node_id: linkedNode },
        { onSuccess: () => onOpenChange(false) },
      )
    } else {
      createFinding.mutate(
        { title, severity, description, node_id: linkedNode },
        { onSuccess: () => onOpenChange(false) },
      )
    }
  }

  const heading = isEditMode ? 'Edit finding' : 'New finding'
  const submitLabel = mutation.isPending
    ? isEditMode
      ? 'Saving…'
      : 'Creating…'
    : isEditMode
      ? 'Save'
      : 'Create'

  return (
    <>
      <DialogHeader>
        <DialogTitle>{heading}</DialogTitle>
        <DialogDescription>
          {isEditMode
            ? 'Update this finding’s details.'
            : 'Describe the finding, pick a severity, and optionally link a graph node.'}
        </DialogDescription>
      </DialogHeader>

      <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        <div className="flex flex-col gap-2">
          <Label htmlFor="finding-title">Title</Label>
          <Input
            id="finding-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            disabled={mutation.isPending}
            placeholder="e.g. Reflected XSS on /search"
            maxLength={512}
          />
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor="finding-severity">Severity</Label>
          <select
            id="finding-severity"
            className={SELECT_CLASS}
            value={severity}
            disabled={mutation.isPending}
            onChange={(e) => setSeverity(e.target.value as Severity)}
          >
            {SEVERITY_ORDER.map((s) => (
              <option key={s} value={s}>
                {SEVERITY_LABELS[s]}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor="finding-description">Description</Label>
          <Textarea
            id="finding-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            disabled={mutation.isPending}
            placeholder="Steps to reproduce, impact, evidence…"
          />
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor="finding-node">Linked graph node (optional)</Label>
          <select
            id="finding-node"
            className={SELECT_CLASS}
            value={nodeId}
            disabled={mutation.isPending}
            onChange={(e) => setNodeId(e.target.value)}
          >
            <option value="">None</option>
            {nodes.map((n) => (
              <option key={n.id} value={n.id}>
                {n.label} ({n.type})
              </option>
            ))}
          </select>
        </div>

        {mutation.error && (
          <p role="alert" className="text-sm text-destructive">
            {mutation.error.message}
          </p>
        )}

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={mutation.isPending}
          >
            Cancel
          </Button>
          <Button type="submit" disabled={mutation.isPending}>
            {submitLabel}
          </Button>
        </DialogFooter>
      </form>
    </>
  )
}

export function FindingDialog({ engagementId, open, onOpenChange, finding }: FindingDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        {open && (
          <FindingForm
            key={`${finding?.id ?? 'create'}-${String(open)}`}
            engagementId={engagementId}
            onOpenChange={onOpenChange}
            finding={finding}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}
