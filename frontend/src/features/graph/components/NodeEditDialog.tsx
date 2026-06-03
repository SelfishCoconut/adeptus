// NodeEditDialog — create or edit a graph node.
// When `node` prop is provided the dialog is in edit mode (pre-fills fields,
// calls useUpdateNode); when absent it is in create mode (calls useCreateNode).
// Properties are edited as raw JSON in a textarea; invalid JSON shows an
// inline error before the mutation is called.
//
// The inner <NodeEditForm> is only mounted while the dialog is open and
// receives its initial values as props.  State is seeded at mount via lazy
// useState initializers — this avoids calling setState inside useEffect which
// triggers the react-hooks/set-state-in-effect lint rule.
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
import { useCreateNode, useUpdateNode } from '../api'
import type { Node } from '../api'
import type { components } from '@/shared/api'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

type NodeType = components['schemas']['NodeType']

const NODE_TYPES: NodeType[] = [
  'host',
  'port',
  'service',
  'url',
  'endpoint',
  'vulnerability',
  'credential',
  'note',
  'attack_path',
]

function serializeProperties(props: Record<string, unknown> | undefined | null): string {
  if (!props || Object.keys(props).length === 0) return '{}'
  return JSON.stringify(props, null, 2)
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface NodeEditDialogProps {
  engagementId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  /** When provided, the dialog is in edit mode and pre-fills these values. */
  node?: Node
}

// ---------------------------------------------------------------------------
// Inner form — only mounted while dialog is open, so lazy initial state
// correctly seeds from props on each open.
// ---------------------------------------------------------------------------

interface NodeEditFormProps {
  engagementId: string
  onOpenChange: (open: boolean) => void
  node?: Node
}

function NodeEditForm({ engagementId, onOpenChange, node }: NodeEditFormProps) {
  const isEditMode = node !== undefined

  const createNode = useCreateNode(engagementId)
  const updateNode = useUpdateNode(engagementId)
  const mutation = isEditMode ? updateNode : createNode

  // Lazy initial state seeded from props — no useEffect needed.
  const [nodeType, setNodeType] = useState<NodeType>(
    () => (node?.type as NodeType | undefined) ?? 'host',
  )
  const [label, setLabel] = useState(() => node?.label ?? '')
  const [propertiesRaw, setPropertiesRaw] = useState(() =>
    serializeProperties(node?.properties as Record<string, unknown> | undefined),
  )
  const [propertiesError, setPropertiesError] = useState<string | null>(null)

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen) {
      mutation.reset()
    }
    onOpenChange(nextOpen)
  }

  function handlePropertiesChange(value: string) {
    setPropertiesRaw(value)
    if (propertiesError) setPropertiesError(null)
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    // Client-side JSON validation
    let parsedProperties: Record<string, unknown>
    try {
      const parsed: unknown = JSON.parse(propertiesRaw)
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        setPropertiesError('Properties must be a JSON object (e.g. {"key": "value"})')
        return
      }
      parsedProperties = parsed as Record<string, unknown>
    } catch {
      setPropertiesError('Invalid JSON — please check your syntax.')
      return
    }

    if (isEditMode && node) {
      updateNode.mutate(
        { nodeId: node.id, label, properties: parsedProperties },
        { onSuccess: () => onOpenChange(false) },
      )
    } else {
      createNode.mutate(
        { type: nodeType, label, properties: parsedProperties },
        { onSuccess: () => onOpenChange(false) },
      )
    }
  }

  const title = isEditMode ? 'Edit Node' : 'Add Node'
  const submitLabel = isEditMode
    ? mutation.isPending
      ? 'Saving…'
      : 'Save'
    : mutation.isPending
      ? 'Creating…'
      : 'Create'

  return (
    <>
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>
          {isEditMode
            ? 'Update the label or properties for this node.'
            : 'Fill in the details to add a new node to the graph.'}
        </DialogDescription>
      </DialogHeader>

      <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        {/* Type selector */}
        <div className="flex flex-col gap-2">
          <Label htmlFor="node-type">Type</Label>
          <select
            id="node-type"
            value={nodeType}
            onChange={(e) => setNodeType(e.target.value as NodeType)}
            disabled={isEditMode || mutation.isPending}
            className="h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs outline-none transition-[color,box-shadow] focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50"
          >
            {NODE_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          {isEditMode && (
            <p className="text-xs text-muted-foreground">
              Node type cannot be changed after creation.
            </p>
          )}
        </div>

        {/* Label input */}
        <div className="flex flex-col gap-2">
          <Label htmlFor="node-label">Label</Label>
          <Input
            id="node-label"
            name="label"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            required
            disabled={mutation.isPending}
            placeholder="e.g. 10.0.0.1, nginx, /api/login"
            maxLength={512}
          />
        </div>

        {/* Properties textarea (raw JSON) */}
        <div className="flex flex-col gap-2">
          <Label htmlFor="node-properties">Properties (JSON)</Label>
          <Textarea
            id="node-properties"
            name="properties"
            value={propertiesRaw}
            onChange={(e) => handlePropertiesChange(e.target.value)}
            disabled={mutation.isPending}
            placeholder='{"key": "value"}'
            className="font-mono text-xs"
            aria-invalid={propertiesError !== null}
          />
          {propertiesError && (
            <p role="alert" className="text-sm text-destructive">
              {propertiesError}
            </p>
          )}
        </div>

        {/* Server error (422 / 409) */}
        {mutation.error && (
          <p role="alert" className="text-sm text-destructive">
            {mutation.error.message}
          </p>
        )}

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => handleOpenChange(false)}
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

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export function NodeEditDialog({
  engagementId,
  open,
  onOpenChange,
  node,
}: NodeEditDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        {/*
         * Mount the inner form only while open.  The key ensures the form
         * remounts (fresh state) each time the dialog opens or the target node
         * changes — no useEffect reset needed.
         */}
        {open && (
          <NodeEditForm
            key={`${node?.id ?? 'create'}-${String(open)}`}
            engagementId={engagementId}
            onOpenChange={onOpenChange}
            node={node}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}
