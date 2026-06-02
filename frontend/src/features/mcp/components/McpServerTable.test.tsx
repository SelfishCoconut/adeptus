import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { McpServerTable } from './McpServerTable'
import { useMe } from '@/features/auth/api'
import { useListMcpServers } from '../api'

vi.mock('@/features/auth/api', () => ({
  useMe: vi.fn(),
}))

vi.mock('../api', () => ({
  useListMcpServers: vi.fn(),
}))

const mockedUseMe = vi.mocked(useMe)
const mockedUseListMcpServers = vi.mocked(useListMcpServers)

// ---------------------------------------------------------------------------
// Shared fixtures
// ---------------------------------------------------------------------------

const ADMIN_USER = {
  id: '00000000-0000-0000-0000-000000000001',
  username: 'alice',
  role: 'admin' as const,
  terms_accepted_at: '2026-01-01T00:00:00Z',
}

const NON_ADMIN_USER = {
  id: '00000000-0000-0000-0000-000000000002',
  username: 'bob',
  role: 'user' as const,
  terms_accepted_at: '2026-01-01T00:00:00Z',
}

const SHELL_EXEC_SERVER = {
  server_name: 'shell-exec',
  status: 'running' as const,
  tools: [
    {
      name: 'run_command',
      weight: 'light' as const,
      capability_flags: ['shell-exec', 'filesystem-write'],
    },
  ],
}

function meResult(data: typeof ADMIN_USER | typeof NON_ADMIN_USER | null, isPending = false) {
  return { data, isPending } as unknown as ReturnType<typeof useMe>
}

function serversResult(overrides: Partial<ReturnType<typeof useListMcpServers>>) {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...overrides,
  } as unknown as ReturnType<typeof useListMcpServers>
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderTable() {
  return render(<McpServerTable />)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('McpServerTable', () => {
  beforeEach(() => {
    mockedUseMe.mockReset()
    mockedUseListMcpServers.mockReset()
  })

  describe('admin-only gate', () => {
    it('renders nothing for a non-admin user', () => {
      mockedUseMe.mockReturnValue(meResult(NON_ADMIN_USER))
      // useListMcpServers should not be called, but mock it defensively
      mockedUseListMcpServers.mockReturnValue(serversResult({ data: [SHELL_EXEC_SERVER] }))

      const { container } = renderTable()

      expect(container).toBeEmptyDOMElement()
    })

    it('renders nothing when there is no session (unauthenticated)', () => {
      mockedUseMe.mockReturnValue(meResult(null))
      mockedUseListMcpServers.mockReturnValue(serversResult({ data: [SHELL_EXEC_SERVER] }))

      const { container } = renderTable()

      expect(container).toBeEmptyDOMElement()
    })
  })

  describe('capability warning', () => {
    it('shows the capability warning notice for admins', () => {
      mockedUseMe.mockReturnValue(meResult(ADMIN_USER))
      mockedUseListMcpServers.mockReturnValue(serversResult({ data: [SHELL_EXEC_SERVER] }))

      renderTable()

      expect(
        screen.getByText(
          'MCP servers run with full system privileges. You are responsible for vetting every server installed here.',
        ),
      ).toBeInTheDocument()
    })
  })

  describe('server table', () => {
    it('renders one server row with correct data', () => {
      mockedUseMe.mockReturnValue(meResult(ADMIN_USER))
      mockedUseListMcpServers.mockReturnValue(serversResult({ data: [SHELL_EXEC_SERVER] }))

      renderTable()

      // Server name and capability flag "shell-exec" both appear — use getAllByText
      const shellExecMatches = screen.getAllByText('shell-exec')
      expect(shellExecMatches.length).toBeGreaterThanOrEqual(1)
      // Tool name
      expect(screen.getByText('run_command')).toBeInTheDocument()
      // Weight badge
      expect(screen.getByText('light')).toBeInTheDocument()
      // Capability flags
      expect(screen.getByText('filesystem-write')).toBeInTheDocument()
      // Status badge
      expect(screen.getByText('running')).toBeInTheDocument()
    })

    it('renders a stopped server with the stopped status badge', () => {
      const stoppedServer = { ...SHELL_EXEC_SERVER, status: 'stopped' as const }
      mockedUseMe.mockReturnValue(meResult(ADMIN_USER))
      mockedUseListMcpServers.mockReturnValue(serversResult({ data: [stoppedServer] }))

      renderTable()

      expect(screen.getByText('stopped')).toBeInTheDocument()
    })

    it('renders empty state when no servers are configured', () => {
      mockedUseMe.mockReturnValue(meResult(ADMIN_USER))
      mockedUseListMcpServers.mockReturnValue(serversResult({ data: [] }))

      renderTable()

      expect(screen.getByText('No MCP servers configured.')).toBeInTheDocument()
    })

    it('shows skeleton while loading', () => {
      mockedUseMe.mockReturnValue(meResult(ADMIN_USER))
      mockedUseListMcpServers.mockReturnValue(serversResult({ isLoading: true }))

      renderTable()

      expect(screen.getByTestId('mcp-server-table-skeleton')).toBeInTheDocument()
    })

    it('shows error alert when query fails', () => {
      mockedUseMe.mockReturnValue(meResult(ADMIN_USER))
      mockedUseListMcpServers.mockReturnValue(
        serversResult({ isError: true, error: new Error('Network timeout') }),
      )

      renderTable()

      expect(screen.getByRole('alert')).toHaveTextContent('Network timeout')
    })
  })
})
