import { McpServerTable } from '@/features/mcp/components/McpServerTable'

export function McpServersPage() {
  return (
    <div className="flex min-h-svh flex-col bg-background text-foreground">
      <header className="border-b px-6 py-4">
        <h1 className="text-xl font-semibold">MCP Servers</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Manage registered Model Context Protocol servers and their declared capabilities.
        </p>
      </header>

      <main className="flex-1 px-6 py-6">
        <McpServerTable />
      </main>
    </div>
  )
}
