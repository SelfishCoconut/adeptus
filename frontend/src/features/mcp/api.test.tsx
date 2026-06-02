import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { mcpServersKey, useExecuteToolRun, useListMcpServers } from './api'
import { api } from '@/shared/api'

vi.mock('@/shared/api', () => ({
  api: { GET: vi.fn(), POST: vi.fn() },
}))

const mockGet = vi.mocked(api.GET)
const mockPost = vi.mocked(api.POST)

type FetchResult = { data?: unknown; error?: unknown; response: { status: number } }
const resolveGet = (value: FetchResult) => mockGet.mockResolvedValue(value as never)
const resolvePost = (value: FetchResult) => mockPost.mockResolvedValue(value as never)

// ---------------------------------------------------------------------------
// Shared fixtures
// ---------------------------------------------------------------------------

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'
const TOOL_RUN_ID = '00000000-0000-0000-0000-000000000002'

const MCP_TOOL_DECLARATION = {
  name: 'run_command',
  weight: 'light' as const,
  capability_flags: ['shell-exec', 'filesystem-write'],
}

const MCP_SERVER_INFO = {
  server_name: 'shell-exec',
  status: 'running' as const,
  tools: [MCP_TOOL_DECLARATION],
}

const TOOL_RUN_CREATE = {
  engagement_id: ENGAGEMENT_ID,
  server_name: 'shell-exec',
  tool_name: 'run_command',
  args: { command: 'echo hello' },
  timeout_seconds: 30,
}

const TOOL_RUN_RESULT = {
  tool_run_id: TOOL_RUN_ID,
  engagement_id: ENGAGEMENT_ID,
  server_name: 'shell-exec',
  tool_name: 'run_command',
  exit_code: 0,
  stdout: 'hello\n',
  stderr: '',
  started_at: '2026-01-01T00:00:00Z',
  finished_at: '2026-01-01T00:00:01Z',
}

// ---------------------------------------------------------------------------
// Wrapper helpers
// ---------------------------------------------------------------------------

function createWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

beforeEach(() => {
  mockGet.mockReset()
  mockPost.mockReset()
})

// ---------------------------------------------------------------------------
// Query key helpers
// ---------------------------------------------------------------------------

describe('query key helpers', () => {
  it('mcpServersKey is a stable tuple', () => {
    expect(mcpServersKey).toEqual(['admin', 'mcp-servers'])
  })
})

// ---------------------------------------------------------------------------
// useListMcpServers
// ---------------------------------------------------------------------------

describe('useListMcpServers', () => {
  it('returns the server list on a 200 response', async () => {
    resolveGet({ data: [MCP_SERVER_INFO], response: { status: 200 } })
    const { result } = renderHook(() => useListMcpServers(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([MCP_SERVER_INFO])
    expect(mockGet).toHaveBeenCalledWith('/api/v1/admin/mcp-servers')
  })

  it('returns an empty array when no servers are configured', async () => {
    resolveGet({ data: [], response: { status: 200 } })
    const { result } = renderHook(() => useListMcpServers(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([])
  })

  it('throws when the caller is not an admin (403)', async () => {
    resolveGet({ data: undefined, error: { detail: 'Forbidden' }, response: { status: 403 } })
    const { result } = renderHook(() => useListMcpServers(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.error).toBeInstanceOf(Error)
  })

  it('throws when error is set even if no data', async () => {
    resolveGet({ error: { detail: 'server error' }, response: { status: 500 } })
    const { result } = renderHook(() => useListMcpServers(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.error).toBeInstanceOf(Error)
  })
})

// ---------------------------------------------------------------------------
// useExecuteToolRun
// ---------------------------------------------------------------------------

describe('useExecuteToolRun', () => {
  it('returns the tool run result on success', async () => {
    resolvePost({ data: TOOL_RUN_RESULT, response: { status: 200 } })
    const { result } = renderHook(() => useExecuteToolRun(), { wrapper: createWrapper() })

    let returned: unknown
    await act(async () => {
      returned = await result.current.mutateAsync(TOOL_RUN_CREATE)
    })

    expect(returned).toEqual(TOOL_RUN_RESULT)
    expect(mockPost).toHaveBeenCalledWith('/api/v1/tool-runs', { body: TOOL_RUN_CREATE })
  })

  it('throws on 403 (caller is not an engagement member)', async () => {
    resolvePost({ error: { detail: 'Forbidden' }, response: { status: 403 } })
    const { result } = renderHook(() => useExecuteToolRun(), { wrapper: createWrapper() })

    await act(async () => {
      await expect(result.current.mutateAsync(TOOL_RUN_CREATE)).rejects.toThrow(
        'Failed to execute tool run',
      )
    })
  })

  it('throws on 503 (MCP server subprocess is not running)', async () => {
    resolvePost({ error: { detail: 'Service Unavailable' }, response: { status: 503 } })
    const { result } = renderHook(() => useExecuteToolRun(), { wrapper: createWrapper() })

    await act(async () => {
      await expect(result.current.mutateAsync(TOOL_RUN_CREATE)).rejects.toThrow(
        'Failed to execute tool run',
      )
    })
  })

  it('throws on 400 (unknown server or tool name)', async () => {
    resolvePost({ error: { detail: 'Bad Request' }, response: { status: 400 } })
    const { result } = renderHook(() => useExecuteToolRun(), { wrapper: createWrapper() })

    await act(async () => {
      await expect(
        result.current.mutateAsync({ ...TOOL_RUN_CREATE, server_name: 'unknown-server' }),
      ).rejects.toThrow('Failed to execute tool run')
    })
  })
})
