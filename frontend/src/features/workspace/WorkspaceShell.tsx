import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { ModeToggle } from '@/components/theme/ModeToggle'
import type { PrivacyMode } from '@/shared/api'
import { PrivacyModeBanner } from './components/PrivacyModeBanner'
import { HealthIndicator } from './HealthIndicator'
import { ToolRunnerPanel } from '@/features/mcp/components/ToolRunnerPanel'
import { GraphPane } from '@/features/graph/components'
import { FindingsPane } from '@/features/findings/components'
import { ApprovalQueue } from '@/features/approvals/components/ApprovalQueue'
import { AutonomyPanel } from '@/features/autonomy/components/AutonomyPanel'
import { AuditPanel } from '@/features/audit/components/AuditPanel'
import { ChatPanel } from '@/features/chat/components/ChatPanel'
import { NodeCertaintyBadge } from '@/features/chat/components/NodeCertaintyBadge'
import { useCertaintyByNode } from '@/features/chat/hooks/useCertaintyByNode'
import { useLowConfidenceThreshold } from '@/features/chat/hooks/useLowConfidenceThreshold'

interface WorkspaceShellProps {
  username: string
  role: string
  onLogout: () => void
  isLoggingOut?: boolean
  privacyMode: PrivacyMode
  /** When provided, the Console pane embeds the tool runner for this engagement. */
  engagementId?: string
}

export function WorkspaceShell({
  username,
  role,
  onLogout,
  isLoggingOut = false,
  privacyMode,
  engagementId,
}: WorkspaceShellProps) {
  // The graph-item certainty overlay (§5.3): a read-only map derived from the caller's own
  // chat turns. The workspace is the composition layer that glues the chat-derived overlay
  // onto the graph pane without either feature depending on the other (ADR-0001 / §8.2).
  const certaintyByNode = useCertaintyByNode(engagementId)
  const threshold = useLowConfidenceThreshold(engagementId)

  // Right-pane tab: the live graph (default) or the engagement's findings (Slice 19).
  const [rightTab, setRightTab] = useState<'graph' | 'findings'>('graph')

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
          <ModeToggle />
          <Button variant="outline" size="sm" onClick={onLogout} disabled={isLoggingOut}>
            {isLoggingOut ? 'Logging out…' : 'Logout'}
          </Button>
        </div>
      </header>
      <PrivacyModeBanner privacyMode={privacyMode} />
      <div className="grid flex-1 grid-cols-2 grid-rows-[1fr_12rem] gap-px overflow-hidden bg-border">
        {/* Left pane: the private per-user AI chat (§11.2 / Slice 11). ChatPanel owns its
            own scrollable list + composer; the privacy banner above stays pinned (§5.5). */}
        <section aria-label="AI chat" className="flex flex-col overflow-hidden bg-background">
          {engagementId ? (
            <ChatPanel engagementId={engagementId} privacyMode={privacyMode} />
          ) : (
            <div className="p-4">
              <h2 className="text-sm font-medium text-muted-foreground">AI chat</h2>
              <p className="mt-3 text-sm text-muted-foreground">
                Select an engagement to chat with the AI.
              </p>
            </div>
          )}
        </section>
        {/* Right pane: the live force-directed graph (§11.2) and the engagement's
            findings (§9, Slice 19), switched by a tab toggle. GraphPane renders the
            interactive Cytoscape canvas (slice 08); FindingsPane the findings table. */}
        <section aria-label="Graph" className="overflow-y-auto bg-background p-4">
          {engagementId ? (
            <div className="flex flex-col gap-3">
              <div role="group" aria-label="Workspace right pane" className="flex items-center gap-1">
                <Button
                  variant={rightTab === 'graph' ? 'default' : 'outline'}
                  size="sm"
                  aria-pressed={rightTab === 'graph'}
                  onClick={() => setRightTab('graph')}
                >
                  Graph
                </Button>
                <Button
                  variant={rightTab === 'findings' ? 'default' : 'outline'}
                  size="sm"
                  aria-pressed={rightTab === 'findings'}
                  onClick={() => setRightTab('findings')}
                >
                  Findings
                </Button>
              </div>
              {rightTab === 'graph' ? (
                <GraphPane
                  engagementId={engagementId}
                  nodeAccessory={(nodeId) => (
                    <NodeCertaintyBadge
                      certainty={certaintyByNode.get(nodeId)}
                      threshold={threshold}
                    />
                  )}
                />
              ) : (
                <FindingsPane engagementId={engagementId} />
              )}
            </div>
          ) : (
            <>
              <h2 className="mb-3 text-sm font-medium text-muted-foreground">Graph</h2>
              <p className="text-sm text-muted-foreground">
                Select an engagement to view the graph.
              </p>
            </>
          )}
        </section>
        <section aria-label="Console" className="col-span-2 overflow-y-auto bg-background p-4">
          <h2 className="mb-3 text-sm font-medium text-muted-foreground">Console</h2>
          {engagementId ? (
            <div className="flex flex-col gap-4">
              <ToolRunnerPanel engagementId={engagementId} />
              {/* The engagement-shared approval queue (§5.2), visible to ALL members so a
                  second member can act on a dangerous proposal (Resolved decision 4). */}
              <ApprovalQueue engagementId={engagementId} />
              {/* Active standing-autonomy grants (§5.2 delegation): the categories whose
                  gated commands auto-approve, each revocable on the spot. */}
              <AutonomyPanel engagementId={engagementId} />
              {/* Admin-only forensic surface (§14): the audit log for this engagement. */}
              {role === 'admin' ? <AuditPanel engagementId={engagementId} /> : null}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Select an engagement to use the tool runner.</p>
          )}
        </section>
      </div>
    </div>
  )
}
