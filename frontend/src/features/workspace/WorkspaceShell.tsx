import { Button } from '@/components/ui/button'
import { HealthIndicator } from './HealthIndicator'

interface WorkspaceShellProps {
  username: string
  role: string
  onLogout: () => void
  isLoggingOut?: boolean
}

export function WorkspaceShell({
  username,
  role,
  onLogout,
  isLoggingOut = false,
}: WorkspaceShellProps) {
  return (
    <div className="flex h-svh flex-col bg-background text-foreground">
      <header className="flex items-center justify-between border-b px-4 py-2">
        <div className="flex items-center gap-3">
          <span className="font-semibold">Adeptus</span>
          <HealthIndicator />
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm">{username}</span>
          <span className="rounded bg-secondary px-2 py-0.5 text-xs uppercase text-secondary-foreground">
            {role}
          </span>
          <Button variant="outline" size="sm" onClick={onLogout} disabled={isLoggingOut}>
            {isLoggingOut ? 'Logging out…' : 'Logout'}
          </Button>
        </div>
      </header>
      <div className="grid flex-1 grid-cols-2 grid-rows-[1fr_12rem] gap-px overflow-hidden bg-border">
        <section aria-label="AI chat" className="bg-background p-4">
          <h2 className="text-sm font-medium text-muted-foreground">AI chat</h2>
        </section>
        <section aria-label="Graph" className="bg-background p-4">
          <h2 className="text-sm font-medium text-muted-foreground">Graph</h2>
        </section>
        <section aria-label="Console" className="col-span-2 bg-background p-4">
          <h2 className="text-sm font-medium text-muted-foreground">Console</h2>
        </section>
      </div>
    </div>
  )
}
