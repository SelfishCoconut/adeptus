import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { GraphHistoryPanel } from './GraphHistoryPanel'
import { useGraphHistory, useUndoNode } from '../api'
import type { Node, GraphHistory } from '../api'

vi.mock('../api', () => ({
  useGraphHistory: vi.fn(),
  useUndoNode: vi.fn(),
}))

const mockedUseGraphHistory = vi.mocked(useGraphHistory)
const mockedUseUndoNode = vi.mocked(useUndoNode)

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

function makeNode(overrides: Partial<Node> = {}): Node {
  return {
    id: '00000000-0000-0000-0000-0000000000aa',
    engagement_id: ENGAGEMENT_ID,
    type: 'host',
    label: '10.0.0.1',
    properties: {},
    deleted: true,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function historyResult(overrides: Partial<ReturnType<typeof useGraphHistory>>) {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...overrides,
  } as unknown as ReturnType<typeof useGraphHistory>
}

function undoNodeResult(overrides: Partial<ReturnType<typeof useUndoNode>> = {}) {
  return {
    mutate: vi.fn(),
    isPending: false,
    ...overrides,
  } as unknown as ReturnType<typeof useUndoNode>
}

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderPanel() {
  return render(<GraphHistoryPanel engagementId={ENGAGEMENT_ID} />)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('GraphHistoryPanel', () => {
  beforeEach(() => {
    mockedUseGraphHistory.mockReset()
    mockedUseUndoNode.mockReset()
    mockedUseUndoNode.mockReturnValue(undoNodeResult())
  })

  describe('loading state', () => {
    it('renders a skeleton while the query is loading', () => {
      mockedUseGraphHistory.mockReturnValue(historyResult({ isLoading: true }))

      renderPanel()

      expect(screen.getByTestId('graph-history-panel-skeleton')).toBeInTheDocument()
    })
  })

  describe('error state', () => {
    it('shows an alert with the error message on query failure', () => {
      mockedUseGraphHistory.mockReturnValue(
        historyResult({ isError: true, error: new Error('Network timeout') }),
      )

      renderPanel()

      expect(screen.getByRole('alert')).toHaveTextContent('Network timeout')
    })

    it('shows a fallback error message when error is not an Error instance', () => {
      mockedUseGraphHistory.mockReturnValue(
        historyResult({ isError: true, error: 'unexpected' as unknown as Error }),
      )

      renderPanel()

      expect(screen.getByRole('alert')).toHaveTextContent('Failed to load graph history.')
    })
  })

  describe('empty state', () => {
    it('renders the empty-state copy when there are no deleted nodes', () => {
      mockedUseGraphHistory.mockReturnValue(
        historyResult({ data: { deleted_nodes: [], node_history: [] } as GraphHistory }),
      )

      renderPanel()

      expect(screen.getByText('No deleted entities.')).toBeInTheDocument()
    })
  })

  describe('deleted node list', () => {
    it('renders a row per deleted node with a type badge and label', () => {
      mockedUseGraphHistory.mockReturnValue(
        historyResult({
          data: {
            deleted_nodes: [
              makeNode({ id: 'a', type: 'host', label: '10.0.0.1' }),
              makeNode({ id: 'b', type: 'vulnerability', label: 'CVE-2024-1234' }),
            ],
            node_history: [],
          } as GraphHistory,
        }),
      )

      renderPanel()

      expect(screen.getByText('10.0.0.1')).toBeInTheDocument()
      expect(screen.getByText('CVE-2024-1234')).toBeInTheDocument()
      // Type badges
      expect(screen.getByText('host')).toBeInTheDocument()
      expect(screen.getByText('vulnerability')).toBeInTheDocument()
    })

    it('calls undoNode.mutate with the correct node id when Undo is clicked', async () => {
      const user = userEvent.setup()
      const mutateFn = vi.fn()
      mockedUseUndoNode.mockReturnValue(undoNodeResult({ mutate: mutateFn }))
      const node = makeNode({ id: 'node-42', label: 'deleted-host' })
      mockedUseGraphHistory.mockReturnValue(
        historyResult({
          data: { deleted_nodes: [node], node_history: [] } as GraphHistory,
        }),
      )

      renderPanel()

      await user.click(screen.getByRole('button', { name: 'Undo' }))
      expect(mutateFn).toHaveBeenCalledWith('node-42')
    })

    it('disables Undo buttons while the undo mutation is pending', () => {
      mockedUseUndoNode.mockReturnValue(undoNodeResult({ isPending: true }))
      mockedUseGraphHistory.mockReturnValue(
        historyResult({
          data: { deleted_nodes: [makeNode()], node_history: [] } as GraphHistory,
        }),
      )

      renderPanel()

      expect(screen.getByRole('button', { name: 'Undo' })).toBeDisabled()
    })

    it('renders an Undo button for each deleted node', async () => {
      const user = userEvent.setup()
      const mutateFn = vi.fn()
      mockedUseUndoNode.mockReturnValue(undoNodeResult({ mutate: mutateFn }))
      mockedUseGraphHistory.mockReturnValue(
        historyResult({
          data: {
            deleted_nodes: [
              makeNode({ id: 'node-1', label: 'host-a' }),
              makeNode({ id: 'node-2', label: 'host-b' }),
            ],
            node_history: [],
          } as GraphHistory,
        }),
      )

      renderPanel()

      const undoButtons = screen.getAllByRole('button', { name: 'Undo' })
      expect(undoButtons).toHaveLength(2)

      // Click the second Undo button
      await user.click(undoButtons[1])
      expect(mutateFn).toHaveBeenCalledWith('node-2')
    })
  })
})
