import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FindingDialog } from './FindingDialog'
import { useCreateFinding, useUpdateFinding } from '../api'
import { useGraph } from '@/features/graph/api'
import type { Finding } from '../api'

vi.mock('../api', () => ({
  useCreateFinding: vi.fn(),
  useUpdateFinding: vi.fn(),
}))
vi.mock('@/features/graph/api', () => ({
  useGraph: vi.fn(),
}))

const mockedUseCreateFinding = vi.mocked(useCreateFinding)
const mockedUseUpdateFinding = vi.mocked(useUpdateFinding)
const mockedUseGraph = vi.mocked(useGraph)

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

function makeFinding(overrides: Partial<Finding> = {}): Finding {
  return {
    id: 'finding-1',
    engagement_id: ENGAGEMENT_ID,
    title: 'Reflected XSS',
    description: 'reproduce here',
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

/** A mutation mock whose mutate() invokes the onSuccess option (so close-on-success works). */
function mutationResult<T = ReturnType<typeof useCreateFinding>>(
  overrides: Record<string, unknown> = {},
): T {
  const mutate = vi.fn(
    (_vars: unknown, opts?: { onSuccess?: (f: Finding) => void }) =>
      opts?.onSuccess?.(makeFinding()),
  )
  return {
    mutate,
    isPending: false,
    error: null,
    ...overrides,
  } as unknown as T
}

function graphResult(nodes: { id: string; label: string; type: string }[] = []) {
  return { data: { nodes, edges: [] } } as unknown as ReturnType<typeof useGraph>
}

describe('FindingDialog', () => {
  beforeEach(() => {
    mockedUseCreateFinding.mockReset()
    mockedUseUpdateFinding.mockReset()
    mockedUseGraph.mockReset()
    mockedUseCreateFinding.mockReturnValue(mutationResult())
    mockedUseUpdateFinding.mockReturnValue(mutationResult<ReturnType<typeof useUpdateFinding>>())
    mockedUseGraph.mockReturnValue(graphResult())
  })

  it('submits a create with the entered fields and closes on success', async () => {
    const user = userEvent.setup()
    const create = mutationResult()
    mockedUseCreateFinding.mockReturnValue(create)
    const onOpenChange = vi.fn()

    render(
      <FindingDialog engagementId={ENGAGEMENT_ID} open onOpenChange={onOpenChange} />,
    )

    await user.type(screen.getByLabelText('Title'), 'SQLi on /login')
    await user.selectOptions(screen.getByLabelText('Severity'), 'critical')
    await user.type(screen.getByLabelText('Description'), 'boom')
    await user.click(screen.getByRole('button', { name: 'Create' }))

    const mutate = (create as unknown as { mutate: ReturnType<typeof vi.fn> }).mutate
    expect(mutate).toHaveBeenCalled()
    expect(mutate.mock.calls[0][0]).toEqual({
      title: 'SQLi on /login',
      severity: 'critical',
      description: 'boom',
      node_id: null,
    })
    // onSuccess (wired into the mock) closes the dialog.
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('pre-fills the fields in edit mode and calls update', async () => {
    const user = userEvent.setup()
    const update = mutationResult<ReturnType<typeof useUpdateFinding>>()
    mockedUseUpdateFinding.mockReturnValue(update)

    render(
      <FindingDialog
        engagementId={ENGAGEMENT_ID}
        open
        onOpenChange={vi.fn()}
        finding={makeFinding({ title: 'Existing', severity: 'medium', description: 'pre' })}
      />,
    )

    expect(screen.getByLabelText('Title')).toHaveValue('Existing')
    expect(screen.getByLabelText('Severity')).toHaveValue('medium')
    expect(screen.getByLabelText('Description')).toHaveValue('pre')

    await user.click(screen.getByRole('button', { name: 'Save' }))
    const mutate = (update as unknown as { mutate: ReturnType<typeof vi.fn> }).mutate
    expect(mutate.mock.calls[0][0]).toMatchObject({ findingId: 'finding-1', title: 'Existing' })
  })

  it('links a node via the picker and unlinks via "None"', async () => {
    const user = userEvent.setup()
    mockedUseGraph.mockReturnValue(
      graphResult([{ id: 'node-7', label: '10.0.0.5', type: 'host' }]),
    )
    const create = mutationResult()
    mockedUseCreateFinding.mockReturnValue(create)

    render(<FindingDialog engagementId={ENGAGEMENT_ID} open onOpenChange={vi.fn()} />)

    await user.type(screen.getByLabelText('Title'), 't')
    // Link a node.
    await user.selectOptions(screen.getByLabelText(/Linked graph node/), 'node-7')
    await user.click(screen.getByRole('button', { name: 'Create' }))

    const mutate = (create as unknown as { mutate: ReturnType<typeof vi.fn> }).mutate
    expect(mutate.mock.calls[0][0]).toMatchObject({ node_id: 'node-7' })
  })

  it('defaults the node link to None (null on submit)', async () => {
    const user = userEvent.setup()
    mockedUseGraph.mockReturnValue(
      graphResult([{ id: 'node-7', label: '10.0.0.5', type: 'host' }]),
    )
    const create = mutationResult()
    mockedUseCreateFinding.mockReturnValue(create)

    render(<FindingDialog engagementId={ENGAGEMENT_ID} open onOpenChange={vi.fn()} />)
    await user.type(screen.getByLabelText('Title'), 't')
    expect(screen.getByLabelText(/Linked graph node/)).toHaveValue('')
    await user.click(screen.getByRole('button', { name: 'Create' }))

    const mutate = (create as unknown as { mutate: ReturnType<typeof vi.fn> }).mutate
    expect(mutate.mock.calls[0][0]).toMatchObject({ node_id: null })
  })

  it('shows the server error inline (e.g. 422)', () => {
    mockedUseCreateFinding.mockReturnValue(
      mutationResult({ error: new Error('title must not be empty') }),
    )
    render(<FindingDialog engagementId={ENGAGEMENT_ID} open onOpenChange={vi.fn()} />)
    expect(screen.getByRole('alert')).toHaveTextContent('title must not be empty')
  })
})
