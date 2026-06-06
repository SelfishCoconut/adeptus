// FindingsPane — composes the findings surface in the workspace: a "New finding"
// button plus the FindingsList, with a shared FindingDialog used for both create
// and edit. Thin view: dialog open/target state is the only local state.
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { FindingDialog } from './FindingDialog'
import { FindingsList } from './FindingsList'
import type { Finding } from '../api'

export interface FindingsPaneProps {
  engagementId: string
}

export function FindingsPane({ engagementId }: FindingsPaneProps) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<Finding | undefined>(undefined)

  function handleNew() {
    setEditing(undefined)
    setDialogOpen(true)
  }

  function handleEdit(finding: Finding) {
    setEditing(finding)
    setDialogOpen(true)
  }

  function handleOpenChange(open: boolean) {
    setDialogOpen(open)
    if (!open) setEditing(undefined)
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-end">
        <Button size="sm" onClick={handleNew}>
          New finding
        </Button>
      </div>
      <FindingsList engagementId={engagementId} onEditFinding={handleEdit} />
      <FindingDialog
        engagementId={engagementId}
        open={dialogOpen}
        onOpenChange={handleOpenChange}
        finding={editing}
      />
    </div>
  )
}
