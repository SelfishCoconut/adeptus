import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import {
  mcpServersKey,
  mcpToolsKey,
  toolQueueKey,
  toolRunsKey,
  useExecuteToolRun,
  useExecuteToolRunAsync,
  useKillToolRun,
  useListMcpServers,
  useListTools,
  useListToolRuns,
  useTimeoutDecision,
} from './api'
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
  async_mode: false,
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
  status: 'completed' as const,
  preset_name: null,
}

const TOOL_DESCRIPTOR = {
  server_name: 'httpx',
  tool_name: 'run_httpx',
  weight: 'light' as const,
  capability_flags: ['network'],
  presets: [
    { name: 'quick', description: 'fast scan', args: { flags: ['-sc', '-title'] } },
    { name: 'full', args: { flags: ['-sc', '-title', '-tech-detect'] } },
  ],
  arg_schema: { type: 'object', properties: { target: { type: 'string' } } },
}

const TOOL_RUN_PAGE = {
  items: [TOOL_RUN_RESULT],
  next_cursor: null,
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

  it('mcpToolsKey is a stable tuple', () => {
    expect(mcpToolsKey).toEqual(['mcp', 'tools'])
  })

  it('toolRunsKey is namespaced by engagement', () => {
    expect(toolRunsKey(ENGAGEMENT_ID)).toEqual(['tool-runs', ENGAGEMENT_ID])
  })
})

// ---------------------------------------------------------------------------
// useListTools
// ---------------------------------------------------------------------------

describe('useListTools', () => {
  it('returns the tool descriptor list on a 200 response', async () => {
    resolveGet({ data: [TOOL_DESCRIPTOR], response: { status: 200 } })
    const { result } = renderHook(() => useListTools(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([TOOL_DESCRIPTOR])
    expect(mockGet).toHaveBeenCalledWith('/api/v1/mcp/tools')
  })

  it('throws when error is set', async () => {
    resolveGet({ error: { detail: 'server error' }, response: { status: 500 } })
    const { result } = renderHook(() => useListTools(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.error).toBeInstanceOf(Error)
  })
})

// ---------------------------------------------------------------------------
// useListToolRuns
// ---------------------------------------------------------------------------

describe('useListToolRuns', () => {
  it('returns the first page on success', async () => {
    resolveGet({ data: TOOL_RUN_PAGE, response: { status: 200 } })
    const { result } = renderHook(() => useListToolRuns(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.pages[0]).toEqual(TOOL_RUN_PAGE)
    expect(mockGet).toHaveBeenCalledWith('/api/v1/tool-runs', {
      params: { query: { engagement_id: ENGAGEMENT_ID, limit: 20 } },
    })
  })

  it('passes the cursor when fetching the next page', async () => {
    resolveGet({
      data: { items: [TOOL_RUN_RESULT], next_cursor: 'CURSOR1' },
      response: { status: 200 },
    })
    const { result } = renderHook(() => useListToolRuns(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.hasNextPage).toBe(true)

    resolveGet({ data: { items: [], next_cursor: null }, response: { status: 200 } })
    await act(async () => {
      await result.current.fetchNextPage()
    })

    expect(mockGet).toHaveBeenLastCalledWith('/api/v1/tool-runs', {
      params: { query: { engagement_id: ENGAGEMENT_ID, limit: 20, cursor: 'CURSOR1' } },
    })
  })

  it('is disabled when engagementId is empty', () => {
    const { result } = renderHook(() => useListToolRuns(''), { wrapper: createWrapper() })
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockGet).not.toHaveBeenCalled()
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

// ---------------------------------------------------------------------------
// useExecuteToolRunAsync
// ---------------------------------------------------------------------------

describe('useExecuteToolRunAsync', () => {
  it('forces async_mode true and returns the partial running result', async () => {
    const runningResult = { ...TOOL_RUN_RESULT, status: 'running', exit_code: null, finished_at: null }
    resolvePost({ data: runningResult, response: { status: 202 } })
    const { result } = renderHook(() => useExecuteToolRunAsync(), { wrapper: createWrapper() })

    let returned: unknown
    await act(async () => {
      returned = await result.current.mutateAsync(TOOL_RUN_CREATE)
    })

    expect(returned).toEqual(runningResult)
    expect(mockPost).toHaveBeenCalledWith('/api/v1/tool-runs', {
      body: { ...TOOL_RUN_CREATE, async_mode: true },
    })
  })

  it('throws on 403 (sandbox guard violation)', async () => {
    resolvePost({ error: { detail: 'Forbidden' }, response: { status: 403 } })
    const { result } = renderHook(() => useExecuteToolRunAsync(), { wrapper: createWrapper() })

    await act(async () => {
      await expect(result.current.mutateAsync(TOOL_RUN_CREATE)).rejects.toThrow(
        'Failed to execute tool run',
      )
    })
  })
})

// ---------------------------------------------------------------------------
// useKillToolRun
// ---------------------------------------------------------------------------

describe('useKillToolRun', () => {
  const killedResult = { ...TOOL_RUN_RESULT, status: 'killed' as const }

  it('POSTs to /kill and returns the updated run result', async () => {
    resolvePost({ data: killedResult, response: { status: 200 } })
    const { result } = renderHook(() => useKillToolRun(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    let returned: unknown
    await act(async () => {
      returned = await result.current.mutateAsync(TOOL_RUN_ID)
    })

    expect(returned).toEqual(killedResult)
    expect(mockPost).toHaveBeenCalledWith('/api/v1/tool-runs/{tool_run_id}/kill', {
      params: { path: { tool_run_id: TOOL_RUN_ID } },
    })
  })

  it('invalidates toolQueueKey on success', async () => {
    resolvePost({ data: killedResult, response: { status: 200 } })

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')

    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )

    const { result } = renderHook(() => useKillToolRun(ENGAGEMENT_ID), { wrapper })

    await act(async () => {
      await result.current.mutateAsync(TOOL_RUN_ID)
    })

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: toolQueueKey(ENGAGEMENT_ID) })
  })

  it('invalidates toolRunsKey on success', async () => {
    resolvePost({ data: killedResult, response: { status: 200 } })

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')

    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )

    const { result } = renderHook(() => useKillToolRun(ENGAGEMENT_ID), { wrapper })

    await act(async () => {
      await result.current.mutateAsync(TOOL_RUN_ID)
    })

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: toolRunsKey(ENGAGEMENT_ID) })
  })

  it('throws on 404 (non-member or unknown run)', async () => {
    resolvePost({ error: { detail: 'Not Found' }, response: { status: 404 } })
    const { result } = renderHook(() => useKillToolRun(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await expect(result.current.mutateAsync(TOOL_RUN_ID)).rejects.toThrow(
        'Failed to kill tool run',
      )
    })
  })
})

// ---------------------------------------------------------------------------
// useTimeoutDecision
// ---------------------------------------------------------------------------

describe('useTimeoutDecision', () => {
  const killedResult = { ...TOOL_RUN_RESULT, status: 'killed' as const }

  it('POSTs a kill decision with the correct body', async () => {
    resolvePost({ data: killedResult, response: { status: 200 } })
    const { result } = renderHook(() => useTimeoutDecision(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    let returned: unknown
    await act(async () => {
      returned = await result.current.mutateAsync({
        toolRunId: TOOL_RUN_ID,
        decision: 'kill',
        extend_seconds: 30,
      })
    })

    expect(returned).toEqual(killedResult)
    expect(mockPost).toHaveBeenCalledWith(
      '/api/v1/tool-runs/{tool_run_id}/timeout-decision',
      {
        params: { path: { tool_run_id: TOOL_RUN_ID } },
        body: { decision: 'kill', extend_seconds: 30 },
      },
    )
  })

  it('POSTs an extend decision and carries extend_seconds in the body', async () => {
    const resumingResult = { ...TOOL_RUN_RESULT, status: 'running' as const }
    resolvePost({ data: resumingResult, response: { status: 200 } })
    const { result } = renderHook(() => useTimeoutDecision(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await result.current.mutateAsync({
        toolRunId: TOOL_RUN_ID,
        decision: 'extend',
        extend_seconds: 60,
      })
    })

    expect(mockPost).toHaveBeenCalledWith(
      '/api/v1/tool-runs/{tool_run_id}/timeout-decision',
      {
        params: { path: { tool_run_id: TOOL_RUN_ID } },
        body: { decision: 'extend', extend_seconds: 60 },
      },
    )
  })

  it('POSTs a wait decision with the correct body', async () => {
    const resumingResult = { ...TOOL_RUN_RESULT, status: 'running' as const }
    resolvePost({ data: resumingResult, response: { status: 200 } })
    const { result } = renderHook(() => useTimeoutDecision(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await result.current.mutateAsync({
        toolRunId: TOOL_RUN_ID,
        decision: 'wait',
        extend_seconds: 30,
      })
    })

    expect(mockPost).toHaveBeenCalledWith(
      '/api/v1/tool-runs/{tool_run_id}/timeout-decision',
      {
        params: { path: { tool_run_id: TOOL_RUN_ID } },
        body: { decision: 'wait', extend_seconds: 30 },
      },
    )
  })

  it('invalidates toolQueueKey on success', async () => {
    resolvePost({ data: killedResult, response: { status: 200 } })

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')

    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )

    const { result } = renderHook(() => useTimeoutDecision(ENGAGEMENT_ID), { wrapper })

    await act(async () => {
      await result.current.mutateAsync({
        toolRunId: TOOL_RUN_ID,
        decision: 'kill',
        extend_seconds: 30,
      })
    })

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: toolQueueKey(ENGAGEMENT_ID) })
  })

  it('invalidates toolRunsKey on success', async () => {
    resolvePost({ data: killedResult, response: { status: 200 } })

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')

    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )

    const { result } = renderHook(() => useTimeoutDecision(ENGAGEMENT_ID), { wrapper })

    await act(async () => {
      await result.current.mutateAsync({
        toolRunId: TOOL_RUN_ID,
        decision: 'kill',
        extend_seconds: 30,
      })
    })

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: toolRunsKey(ENGAGEMENT_ID) })
  })

  it('throws on 404 (non-member or unknown run)', async () => {
    resolvePost({ error: { detail: 'Not Found' }, response: { status: 404 } })
    const { result } = renderHook(() => useTimeoutDecision(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await expect(
        result.current.mutateAsync({
          toolRunId: TOOL_RUN_ID,
          decision: 'kill',
          extend_seconds: 30,
        }),
      ).rejects.toThrow('Failed to submit timeout decision')
    })
  })

  it('throws on 409 (run is not awaiting a decision)', async () => {
    resolvePost({ error: { detail: 'Run is not awaiting a decision' }, response: { status: 409 } })
    const { result } = renderHook(() => useTimeoutDecision(ENGAGEMENT_ID), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await expect(
        result.current.mutateAsync({
          toolRunId: TOOL_RUN_ID,
          decision: 'extend',
          extend_seconds: 30,
        }),
      ).rejects.toThrow('Failed to submit timeout decision')
    })
  })

})
