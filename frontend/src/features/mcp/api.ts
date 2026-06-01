import { useMutation, useQuery } from '@tanstack/react-query'
import {
  api,
  type McpServerInfo,
  type ToolRunCreate,
  type ToolRunResult,
} from '@/shared/api'

// --- Query key constants ---

export const mcpServersKey = ['admin', 'mcp-servers'] as const

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
