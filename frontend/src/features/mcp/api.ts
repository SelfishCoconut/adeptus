import { useInfiniteQuery, useMutation, useQuery } from '@tanstack/react-query'
import {
  api,
  type McpServerInfo,
  type ToolDescriptor,
  type ToolRunCreate,
  type ToolRunPage,
  type ToolRunResult,
} from '@/shared/api'

// --- Query key constants ---

export const mcpServersKey = ['admin', 'mcp-servers'] as const
export const mcpToolsKey = ['mcp', 'tools'] as const

export function toolRunsKey(engagementId: string) {
  return ['tool-runs', engagementId] as const
}

export function toolRunKey(toolRunId: string) {
  return ['tool-run', toolRunId] as const
}

// --- Queries ---

export function useListMcpServers() {
  return useQuery<McpServerInfo[]>({
    queryKey: mcpServersKey,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/admin/mcp-servers')
      if (error || !data) throw new Error('Failed to load MCP servers')
      return data
    },
    staleTime: 30_000,
  })
}

/** List all tools across running MCP servers, enriched with presets + arg schema. */
export function useListTools() {
  return useQuery<ToolDescriptor[]>({
    queryKey: mcpToolsKey,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/mcp/tools')
      if (error || !data) throw new Error('Failed to load tools')
      return data
    },
    staleTime: 60_000,
  })
}

const DEFAULT_PAGE_LIMIT = 20

/** Paginated (keyset) list of tool runs for an engagement, newest first. */
export function useListToolRuns(engagementId: string, options?: { enabled?: boolean }) {
  return useInfiniteQuery<ToolRunPage>({
    queryKey: toolRunsKey(engagementId),
    enabled: (options?.enabled ?? true) && Boolean(engagementId),
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }) => {
      const { data, error } = await api.GET('/api/v1/tool-runs', {
        params: {
          query: {
            engagement_id: engagementId,
            limit: DEFAULT_PAGE_LIMIT,
            ...(pageParam ? { cursor: pageParam as string } : {}),
          },
        },
      })
      if (error || !data) throw new Error('Failed to load tool runs')
      return data
    },
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  })
}

/** Fetch a single (historical) tool run by id, including its stored output. */
export function useToolRun(toolRunId: string | null) {
  return useQuery<ToolRunResult>({
    queryKey: toolRunKey(toolRunId ?? ''),
    enabled: Boolean(toolRunId),
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/tool-runs/{tool_run_id}', {
        params: { path: { tool_run_id: toolRunId as string } },
      })
      if (error || !data) throw new Error('Failed to load tool run')
      return data
    },
  })
}

// --- Mutations ---

export function useExecuteToolRun() {
  return useMutation<ToolRunResult, Error, ToolRunCreate>({
    mutationFn: async (body) => {
      const { data, error } = await api.POST('/api/v1/tool-runs', { body })
      if (error || !data) throw new Error('Failed to execute tool run')
      return data
    },
  })
}

/**
 * Execute a tool run in async/streaming mode. The endpoint responds 202 with a
 * partial result (status `running`); the caller then opens the WebSocket to
 * consume output. `async_mode` is forced true regardless of the supplied body.
 */
export function useExecuteToolRunAsync() {
  return useMutation<ToolRunResult, Error, ToolRunCreate>({
    mutationFn: async (body) => {
      const { data, error } = await api.POST('/api/v1/tool-runs', {
        body: { ...body, async_mode: true },
      })
      if (error || !data) throw new Error('Failed to execute tool run')
      return data
    },
  })
}
