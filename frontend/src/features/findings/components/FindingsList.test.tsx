import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FindingsList } from './FindingsList'
import { useDeleteFinding, useFindings } from '../api'
import { useGraph } from '@/features/graph/api'
import type { Finding } from '../api'

vi.mock('../api', () => ({
  useFindings: vi.fn(),
  useDeleteFinding: vi.fn(),
}))
vi.mock('@/features/graph/api', () => ({
  useGraph: vi.fn(),
}))
// Stub StatusPickers so this test stays focused on the list (the pickers have
// their own test and pull in their own mutation hooks).
vi.mock('./StatusPickers', () => ({
  StatusPickers: ({ finding }: { finding: Finding }) => (
    <span data-testid={`status-${finding.id}`}>
      {finding.verification_status}/{finding.remediation_status}
    </span>
  ),
}))

const mockedUseFindings = vi.mocked(useFindings)
const mockedUseDeleteFinding = vi.mocked(useDeleteFinding)
const mockedUseGraph = vi.mocked(useGraph)

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

function makeFinding(overrides: Partial<Finding> = {}): Finding {
  return {
    id: 'finding-1',
    engagement_id: ENGAGEMENT_ID,
    title: 'Reflected XSS on /search',
    description: '',
    severity: 'high',
    verification_status: 'unverified',
    remediation_status: 'open',
    node_id: null,
    deleted: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function findingsResult(overrides: Partial<ReturnType<typeof useFindings>>) {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...overrides,
  } as unknown as ReturnType<typeof useFindings>
}

function graphResult(nodes: { id: string; label: string }[] = []) {
  return { data: { nodes, edges: [] } } as unknown as ReturnType<typeof useGraph>
}

function deleteResult(overrides: Record<string, unknown> = {}) {
  return { mutate: vi.fn(), isPending: false, ...overrides } as unknown as ReturnType<
    typeof useDeleteFinding
  >
}

function renderList(onEditFinding = vi.fn()) {
  return render(<FindingsList engagementId={ENGAGEMENT_ID} onEditFinding={onEditFinding} />)
}

describe('FindingsList', () => {
  beforeEach(() => {
    mockedUseFindings.mockReset()
    mockedUseDeleteFinding.mockReset()
    mockedUseGraph.mockReset()
    mockedUseDeleteFinding.mockReturnValue(deleteResult())
    mockedUseGraph.mockReturnValue(graphResult())
  })

  it('renders a skeleton while loading', () => {
    mockedUseFindings.mockReturnValue(findingsResult({ isLoading: true }))
    renderList()
    expect(screen.getByTestId('findings-list-skeleton')).toBeInTheDocument()
  })

  it('shows an alert on query failure', () => {
    mockedUseFindings.mockReturnValue(
      findingsResult({ isError: true, error: new Error('Boom') }),
    )
    renderList()
    expect(screen.getByRole('alert')).toHaveTextContent('Boom')
  })

  it('renders the empty state when there are no findings', () => {
    mockedUseFindings.mockReturnValue(findingsResult({ data: { items: [] } }))
    renderList()
    expect(screen.getByText('No findings yet — add one.')).toBeInTheDocument()
  })

  it('renders a row per finding with a severity badge, title and status', () => {
    mockedUseFindings.mockReturnValue(
      findingsResult({
        data: {
          items: [
            makeFinding({ id: 'a', severity: 'critical', title: 'SQLi' }),
            makeFinding({ id: 'b', severity: 'low', title: 'Verbose error' }),
          ],
        },
      }),
    )
    renderList()
    expect(screen.getByText('SQLi')).toBeInTheDocument()
    expect(screen.getByText('Verbose error')).toBeInTheDocument()
    expect(screen.getByText('Critical')).toBeInTheDocument()
    expect(screen.getByText('Low')).toBeInTheDocument()
    expect(screen.getByTestId('status-a')).toHaveTextContent('unverified/open')
  })

  it('resolves the linked-node label from the graph, or shows a dash', () => {
    mockedUseGraph.mockReturnValue(graphResult([{ id: 'node-9', label: '10.0.0.5' }]))
    mockedUseFindings.mockReturnValue(
      findingsResult({
        data: {
          items: [
            makeFinding({ id: 'a', node_id: 'node-9' }),
            makeFinding({ id: 'b', node_id: null }),
          ],
        },
      }),
    )
    renderList()
    expect(screen.getByText('10.0.0.5')).toBeInTheDocument()
    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('calls onEditFinding with the finding when Edit is clicked', async () => {
    const user = userEvent.setup()
    const onEditFinding = vi.fn()
    const finding = makeFinding({ id: 'edit-me' })
    mockedUseFindings.mockReturnValue(findingsResult({ data: { items: [finding] } }))
    renderList(onEditFinding)
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    expect(onEditFinding).toHaveBeenCalledWith(finding)
  })

  it('calls deleteFinding.mutate with the id when Delete is clicked', async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()
    mockedUseDeleteFinding.mockReturnValue(deleteResult({ mutate }))
    mockedUseFindings.mockReturnValue(
      findingsResult({ data: { items: [makeFinding({ id: 'del-me' })] } }),
    )
    renderList()
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(mutate).toHaveBeenCalledWith('del-me')
  })

  it('disables Delete while a deletion is pending', () => {
    mockedUseDeleteFinding.mockReturnValue(deleteResult({ isPending: true }))
    mockedUseFindings.mockReturnValue(
      findingsResult({ data: { items: [makeFinding()] } }),
    )
    renderList()
    expect(screen.getByRole('button', { name: 'Delete' })).toBeDisabled()
  })
})
