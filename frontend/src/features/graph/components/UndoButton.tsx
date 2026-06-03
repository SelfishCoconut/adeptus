// UndoButton — the personal undo-stack toolbar control (Slice 09, §8.2 layer 2).
//
// Shows "Undo (N)" where N is the caller's personal undo-stack depth, disabled
// when the stack is empty. Clicking (or Ctrl/Cmd+Z) pops the most recent
// still-valid personal write. When the server skips a stale entry (a teammate —
// or the user — changed the target since), an inline message is surfaced; the
// undo never silently reverts that later work. An empty-stack pop is not an
// error — the button simply stays disabled.
//
// This is the *personal* layer; it is distinct from the per-entity undo in the
// History panel (Slice 07) and does not affect it (Risk 5).
import { useCallback, useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { usePopUndoStack, useUndoStack } from '../api'

export interface UndoButtonProps {
  engagementId: string
  /**
   * When true (e.g. the node edit dialog or a text input is focused at the
   * pane level), the Ctrl/Cmd+Z keyboard shortcut is ignored so it doesn't
   * hijack typing/editing (Risk 7).
   */
  shortcutDisabled?: boolean
}

const STALE_MESSAGE = 'Skipped — a teammate changed this since'

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || target.isContentEditable
}

export function UndoButton({ engagementId, shortcutDisabled = false }: UndoButtonProps) {
  const { data: stack } = useUndoStack(engagementId)
  const pop = usePopUndoStack(engagementId)
  const [message, setMessage] = useState<string | null>(null)

  const depth = stack?.depth ?? 0
  const disabled = depth === 0 || pop.isPending

  const handleUndo = useCallback(async () => {
    if (depth === 0 || pop.isPending) return
    setMessage(null)
    const result = await pop.mutateAsync()
    // A teammate changed the target since — surfaced, never silently reverted.
    if (result.skipped_stale.length > 0) {
      setMessage(STALE_MESSAGE)
    }
    // result.undone === null with no skips → nothing left to undo; the button
    // just disables (depth refreshes to 0). No error message in that case.
  }, [depth, pop])

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const isUndoChord =
        (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z' && !event.shiftKey
      if (!isUndoChord) return
      if (shortcutDisabled) return
      if (isEditableTarget(event.target)) return
      event.preventDefault()
      void handleUndo()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [handleUndo, shortcutDisabled])

  return (
    <div className="flex items-center gap-2">
      <Button
        variant="outline"
        size="sm"
        onClick={() => void handleUndo()}
        disabled={disabled}
        aria-label="Undo my last change"
      >
        Undo ({depth})
      </Button>
      {message && (
        <span role="status" className="text-sm text-muted-foreground">
          {message}
        </span>
      )}
    </div>
  )
}
