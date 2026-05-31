import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { EngagementList } from '../components/EngagementList'
import { NewEngagementDialog } from '../components/NewEngagementDialog'

export function EngagementsPage() {
  const [dialogOpen, setDialogOpen] = useState(false)

  return (
    <div className="flex min-h-svh flex-col bg-background text-foreground">
      <header className="flex items-center justify-between border-b px-6 py-4">
        <h1 className="text-xl font-semibold">Adeptus</h1>
        <Button onClick={() => setDialogOpen(true)}>New Engagement</Button>
      </header>

      <main className="flex-1 px-6 py-6">
        <EngagementList />
      </main>

      <NewEngagementDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </div>
  )
}
