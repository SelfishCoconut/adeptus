// Pure presentational friction modal for cloud egress (Slice 14, §5.1). It is shown — by the
// composer/panel — only when a cloud_enabled send matched a likely-secret pattern, BEFORE the
// POST. It deliberately receives only the matched category NAMES, never the message content or
// the matched value (§5.5): there is nothing here that could render a secret. On confirm the
// caller re-sends the message UNMODIFIED (friction, not redaction).
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { egressCategoryLabel } from '../egressScan'

interface EgressConfirmModalProps {
  open: boolean
  /** Matched secret-pattern category NAMES (never the matched value). */
  categories: string[]
  onConfirm: () => void
  onCancel: () => void
}

export function EgressConfirmModal({
  open,
  categories,
  onConfirm,
  onCancel,
}: EgressConfirmModalProps) {
  const labels = categories.map(egressCategoryLabel)
  return (
    // Closing via Escape / overlay / the X is a cancel — nothing is sent.
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onCancel()
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>This message may contain a secret</DialogTitle>
          <DialogDescription>
            It looks like this message may contain a secret (matched: {labels.join(', ')}). It
            will be sent <strong>unmodified</strong> to the cloud model. Send anyway?
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onCancel}>
            Cancel
          </Button>
          <Button type="button" onClick={onConfirm}>
            Send anyway
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
