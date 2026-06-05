import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import type { GraphSubsetNode, GraphSubsetReason } from '@/shared/api'
import { useChatTurnDebug } from '../api'

interface AiDebugPanelProps {
  engagementId: string
  /** The assistant message id whose §14 debug record to show. */
  messageId: string
}

// Render order = inclusion priority (pinned weighted first). A node is grouped under its
// highest-priority reason but shows a chip for EVERY reason it carries, so it appears once.
const REASON_ORDER: GraphSubsetReason[] = ['pinned', 'mentioned', 'recent', 'keyword']

const REASON_LABELS: Record<GraphSubsetReason, string> = {
  pinned: 'Pinned',
  mentioned: '@-mentioned',
  recent: 'Recently touched',
  keyword: 'Keyword match',
}

function primaryReason(reasons: GraphSubsetReason[]): GraphSubsetReason {
  return REASON_ORDER.find((r) => reasons.includes(r)) ?? reasons[0]
}

function NodeRow({ node }: { node: GraphSubsetNode }) {
  return (
    <li className="flex flex-wrap items-center gap-1.5 text-xs">
      <span className="font-mono text-muted-foreground">({node.type})</span>
      <span className="font-medium">{node.label}</span>
      {[...node.reasons]
        .sort((a, b) => REASON_ORDER.indexOf(a) - REASON_ORDER.indexOf(b))
        .map((reason) => (
          <Badge key={reason} variant="secondary" className="px-1 py-0 text-[10px]">
            {reason}
          </Badge>
        ))}
    </li>
  )
}

/**
 * The §14 AI debug panel for one assistant turn. Lazily fetches the turn's debug record
 * (the exact §5.3 relevant subset injected, the raw prompt, and the model output) and
 * renders the injected nodes grouped by inclusion reason, the injected edges, the
 * node/edge counts, and collapsible raw-prompt + model-output blocks.
 *
 * "Tool calls" (§14) are out of scope until the AI can call tools (Slice 16) and are
 * intentionally omitted here.
 */
export function AiDebugPanel({ engagementId, messageId }: AiDebugPanelProps) {
  const { data, isLoading, isError } = useChatTurnDebug(engagementId, messageId)

  if (isLoading) {
    return (
      <div aria-label="AI debug panel" className="space-y-2 rounded-md border bg-card p-3">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-3/4" />
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div
        aria-label="AI debug panel"
        role="alert"
        className="rounded-md border bg-card p-3 text-xs text-destructive"
      >
        Couldn’t load the AI debug record for this turn.
      </div>
    )
  }

  const { nodes, edges } = data
  const labelById = new Map(nodes.map((n) => [n.id, n.label]))

  return (
    <section
      aria-label="AI debug panel"
      className="space-y-3 rounded-md border bg-card p-3 text-xs text-foreground"
    >
      <p className="font-medium text-muted-foreground">
        {nodes.length} {nodes.length === 1 ? 'node' : 'nodes'} · {edges.length}{' '}
        {edges.length === 1 ? 'edge' : 'edges'} injected
      </p>

      {nodes.length === 0 ? (
        <p className="text-muted-foreground">No graph entities matched this turn.</p>
      ) : (
        <div className="space-y-2">
          {REASON_ORDER.map((reason) => {
            const group = nodes.filter((n) => primaryReason(n.reasons) === reason)
            if (group.length === 0) return null
            return (
              <div key={reason}>
                <h4 className="mb-1 font-semibold uppercase tracking-wide text-muted-foreground">
                  {REASON_LABELS[reason]}
                </h4>
                <ul className="space-y-1">
                  {group.map((node) => (
                    <NodeRow key={node.id} node={node} />
                  ))}
                </ul>
              </div>
            )
          })}
        </div>
      )}

      {edges.length > 0 ? (
        <div>
          <h4 className="mb-1 font-semibold uppercase tracking-wide text-muted-foreground">
            Edges
          </h4>
          <ul className="space-y-1 font-mono">
            {edges.map((edge) => (
              <li key={edge.id}>
                {labelById.get(edge.source_id) ?? edge.source_id} —{edge.relation}→{' '}
                {labelById.get(edge.target_id) ?? edge.target_id}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <details>
        <summary className="cursor-pointer font-semibold text-muted-foreground">
          Raw prompt
        </summary>
        <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-muted p-2">
          {data.raw_prompt || '(empty)'}
        </pre>
      </details>

      <details>
        <summary className="cursor-pointer font-semibold text-muted-foreground">
          Model output
        </summary>
        <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-muted p-2">
          {data.model_output || '(empty)'}
        </pre>
      </details>
    </section>
  )
}
