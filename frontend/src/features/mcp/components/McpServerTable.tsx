import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { useMe } from '@/features/auth/api'
import { useListMcpServers } from '../api'

const CAPABILITY_WARNING =
  'MCP servers run with full system privileges. You are responsible for vetting every server installed here.'

export function McpServerTable() {
  const me = useMe()

  // Defensive admin gate: non-admins see nothing.
  // Route-level guard is added in task 4; this component enforces it independently.
  if (!me.data || me.data.role !== 'admin') {
    return null
  }

  return <McpServerTableInner />
}

function McpServerTableInner() {
  const { data, isLoading, isError, error } = useListMcpServers()

  if (isLoading) {
    return (
      <div data-testid="mcp-server-table-skeleton" className="flex flex-col gap-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    )
  }

  if (isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {error instanceof Error ? error.message : 'Failed to load MCP servers.'}
      </p>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
        {CAPABILITY_WARNING}
      </p>
      {!data || data.length === 0 ? (
        <p className="text-sm text-muted-foreground">No MCP servers configured.</p>
      ) : (
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/50">
                <th className="px-4 py-3 text-left font-medium text-muted-foreground">
                  Server
                </th>
                <th className="px-4 py-3 text-left font-medium text-muted-foreground">
                  Tool
                </th>
                <th className="px-4 py-3 text-left font-medium text-muted-foreground">
                  Weight
                </th>
                <th className="px-4 py-3 text-left font-medium text-muted-foreground">
                  Capability flags
                </th>
                <th className="px-4 py-3 text-left font-medium text-muted-foreground">
                  Status
                </th>
              </tr>
            </thead>
            <tbody>
              {data.flatMap((server) =>
                server.tools.length === 0
                  ? [
                      <tr key={server.server_name} className="border-b last:border-0">
                        <td className="px-4 py-3 font-medium">{server.server_name}</td>
                        <td className="px-4 py-3 text-muted-foreground" colSpan={3}>
                          —
                        </td>
                        <td className="px-4 py-3">
                          <StatusBadge status={server.status} />
                        </td>
                      </tr>,
                    ]
                  : server.tools.map((tool, idx) => (
                      <tr key={`${server.server_name}-${tool.name}`} className="border-b last:border-0">
                        {idx === 0 && (
                          <td
                            className="px-4 py-3 font-medium align-top"
                            rowSpan={server.tools.length}
                          >
                            {server.server_name}
                          </td>
                        )}
                        <td className="px-4 py-3">{tool.name}</td>
                        <td className="px-4 py-3">
                          <Badge variant="outline">{tool.weight}</Badge>
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex flex-wrap gap-1">
                            {tool.capability_flags.map((flag) => (
                              <Badge key={flag} variant="secondary">
                                {flag}
                              </Badge>
                            ))}
                          </div>
                        </td>
                        {idx === 0 && (
                          <td className="px-4 py-3 align-top" rowSpan={server.tools.length}>
                            <StatusBadge status={server.status} />
                          </td>
                        )}
                      </tr>
                    )),
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function StatusBadge({ status }: { status: 'running' | 'stopped' }) {
  return (
    <Badge variant={status === 'running' ? 'default' : 'destructive'}>{status}</Badge>
  )
}
