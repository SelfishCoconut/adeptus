import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { NodeEditDialog } from './NodeEditDialog'
import { useCreateNode, useUpdateNode } from '../api'
import type { Node } from '../api'

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

vi.mock('../api', () => ({
  useCreateNode: vi.fn(),
  useUpdateNode: vi.fn(),
}))

const mockedUseCreateNode = vi.mocked(useCreateNode)
const mockedUseUpdateNode = vi.mocked(useUpdateNode)

// ---------------------------------------------------------------------------
// Helpers to build fake mutation results
// ---------------------------------------------------------------------------

// Returns an object shaped like a UseMutationResult.  Typed as `unknown` so
// the caller can cast to either ReturnType<typeof useCreateNode> or
// ReturnType<typeof useUpdateNode> without TypeScript complaining about the
// incompatible `mutate` variables type (NodeCreate vs { nodeId } & NodeUpdate).
function makeMutation(
  overrides: {
    // Use the widest possible signature so both NodeCreate and NodeUpdate
    // mutate functions (which have incompatible variable types) can be passed.
    mutate?: (...args: never[]) => unknown
    isPending?: boolean
    error?: Error | null
    reset?: () => void
  } = {},
): unknown {
  return {
    mutate: overrides.mutate ?? vi.fn(),
    mutateAsync: vi.fn(),
    isPending: overrides.isPending ?? false,
    error: overrides.error ?? null,
    isError: !!(overrides.error),
    isIdle: true,
    isSuccess: false,
    data: undefined,
    reset: overrides.reset ?? vi.fn(),
    status: 'idle' as const,
    variables: undefined,
    context: undefined,
    failureCount: 0,
    failureReason: null,
    isPaused: false,
    submittedAt: 0,
  }
}

function asCreateMutation(m: unknown) {
  return m as ReturnType<typeof useCreateNode>
}

function asUpdateMutation(m: unknown) {
  return m as ReturnType<typeof useUpdateNode>
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

function makeNode(overrides: Partial<Node> = {}): Node {
  return {
    id: '00000000-0000-0000-0000-0000000000aa',
    engagement_id: ENGAGEMENT_ID,
    type: 'host',
    label: '10.0.0.5',
    properties: { os: 'linux' },
    deleted: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Render helpers
// ---------------------------------------------------------------------------

function renderCreateDialog(onOpenChange = vi.fn()) {
  return {
    onOpenChange,
    ...render(
      <NodeEditDialog
        engagementId={ENGAGEMENT_ID}
        open={true}
        onOpenChange={onOpenChange}
      />,
    ),
  }
}

function renderEditDialog(node: Node, onOpenChange = vi.fn()) {
  return {
    onOpenChange,
    ...render(
      <NodeEditDialog
        engagementId={ENGAGEMENT_ID}
        open={true}
        onOpenChange={onOpenChange}
        node={node}
      />,
    ),
  }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('NodeEditDialog', () => {
  beforeEach(() => {
    mockedUseCreateNode.mockReset()
    mockedUseUpdateNode.mockReset()
    mockedUseCreateNode.mockReturnValue(asCreateMutation(makeMutation()))
    mockedUseUpdateNode.mockReturnValue(asUpdateMutation(makeMutation()))
  })

  // -------------------------------------------------------------------------
  // Create mode
  // -------------------------------------------------------------------------

  describe('create mode (no node prop)', () => {
    it('renders "Add Node" title with type, label, and properties fields', () => {
      renderCreateDialog()

      expect(screen.getByRole('heading', { name: /add node/i })).toBeInTheDocument()
      expect(screen.getByLabelText(/type/i)).toBeInTheDocument()
      expect(screen.getByLabelText(/label/i)).toBeInTheDocument()
      expect(screen.getByLabelText(/properties/i)).toBeInTheDocument()
    })

    it('type selector is enabled in create mode', () => {
      renderCreateDialog()

      const select = screen.getByLabelText(/type/i)
      expect(select).not.toBeDisabled()
    })

    it('submits with the correct body when form is filled', async () => {
      const user = userEvent.setup()
      const mutate = vi.fn()
      mockedUseCreateNode.mockReturnValue(asCreateMutation(makeMutation({ mutate })))

      renderCreateDialog()

      // Change type to 'service'
      await user.selectOptions(screen.getByLabelText(/type/i), 'service')
      // Fill label
      await user.clear(screen.getByLabelText(/label/i))
      await user.type(screen.getByLabelText(/label/i), 'nginx')
      // Fill valid JSON properties — use fireEvent.change because userEvent.type
      // interprets '{' as a keyboard modifier descriptor.
      fireEvent.change(screen.getByLabelText(/properties \(json\)/i), {
        target: { value: '{"port": 80}' },
      })

      await user.click(screen.getByRole('button', { name: /create/i }))

      expect(mutate).toHaveBeenCalledOnce()
      expect(mutate).toHaveBeenCalledWith(
        { type: 'service', label: 'nginx', properties: { port: 80 } },
        expect.objectContaining({ onSuccess: expect.any(Function) }),
      )
    })

    it('calls onOpenChange(false) on successful create', async () => {
      const user = userEvent.setup()
      const onOpenChange = vi.fn()
      const mutate = vi.fn((_body: unknown, opts?: Record<string, unknown>) => {
        const cb = opts?.['onSuccess']
        if (typeof cb === 'function') cb()
      })
      mockedUseCreateNode.mockReturnValue(asCreateMutation(makeMutation({ mutate })))

      render(
        <NodeEditDialog
          engagementId={ENGAGEMENT_ID}
          open={true}
          onOpenChange={onOpenChange}
          // no node → create mode
        />,
      )

      await user.type(screen.getByLabelText(/label/i), 'target-host')
      await user.click(screen.getByRole('button', { name: /create/i }))

      expect(onOpenChange).toHaveBeenCalledWith(false)
    })

    it('shows inline error for invalid JSON in properties', async () => {
      const user = userEvent.setup()
      renderCreateDialog()

      await user.clear(screen.getByLabelText(/properties \(json\)/i))
      await user.type(screen.getByLabelText(/properties \(json\)/i), 'not json')
      await user.type(screen.getByLabelText(/label/i), 'host-1')
      await user.click(screen.getByRole('button', { name: /create/i }))

      expect(screen.getByRole('alert')).toHaveTextContent(/invalid json/i)
      // Mutation must NOT have been called — use the last mocked return value
      const lastResult = mockedUseCreateNode.mock.results[mockedUseCreateNode.mock.results.length - 1]
      const lastMutation = lastResult?.value as ReturnType<typeof useCreateNode> | undefined
      expect(lastMutation?.mutate).not.toHaveBeenCalled()
    })

    it('shows inline error when properties JSON is a non-object (array)', async () => {
      const user = userEvent.setup()
      renderCreateDialog()

      // fireEvent.change because '[' is also a special char in userEvent.type
      fireEvent.change(screen.getByLabelText(/properties \(json\)/i), {
        target: { value: '[1, 2]' },
      })
      await user.type(screen.getByLabelText(/label/i), 'host-1')
      await user.click(screen.getByRole('button', { name: /create/i }))

      expect(screen.getByRole('alert')).toHaveTextContent(/properties must be a json object/i)
    })
  })

  // -------------------------------------------------------------------------
  // Edit mode
  // -------------------------------------------------------------------------

  describe('edit mode (node prop provided)', () => {
    it('renders "Edit Node" title', () => {
      renderEditDialog(makeNode())

      expect(screen.getByRole('heading', { name: /edit node/i })).toBeInTheDocument()
    })

    it('pre-fills label from the node', () => {
      renderEditDialog(makeNode({ label: '192.168.1.100' }))

      expect(screen.getByLabelText(/label/i)).toHaveValue('192.168.1.100')
    })

    it('pre-fills node type and shows it as disabled', () => {
      renderEditDialog(makeNode({ type: 'service' }))

      const typeSelect = screen.getByLabelText(/type/i)
      expect(typeSelect).toHaveValue('service')
      expect(typeSelect).toBeDisabled()
    })

    it('pre-fills properties as formatted JSON', () => {
      renderEditDialog(makeNode({ properties: { os: 'linux' } }))

      const textarea = screen.getByLabelText(/properties \(json\)/i)
      // The value should be valid parseable JSON containing the os key
      const parsed: unknown = JSON.parse((textarea as HTMLTextAreaElement).value)
      expect(parsed).toEqual({ os: 'linux' })
    })

    it('calls useUpdateNode mutate with label/properties on submit', async () => {
      const user = userEvent.setup()
      const mutate = vi.fn()
      mockedUseUpdateNode.mockReturnValue(asUpdateMutation(makeMutation({ mutate })))

      const node = makeNode({ id: 'node-99', label: 'old-label', properties: {} })
      renderEditDialog(node)

      await user.clear(screen.getByLabelText(/label/i))
      await user.type(screen.getByLabelText(/label/i), 'new-label')
      // fireEvent.change because '{' is a keyboard modifier in userEvent.type
      fireEvent.change(screen.getByLabelText(/properties \(json\)/i), {
        target: { value: '{"version": "1.2.3"}' },
      })

      await user.click(screen.getByRole('button', { name: /save/i }))

      expect(mutate).toHaveBeenCalledOnce()
      expect(mutate).toHaveBeenCalledWith(
        { nodeId: 'node-99', label: 'new-label', properties: { version: '1.2.3' } },
        expect.objectContaining({ onSuccess: expect.any(Function) }),
      )
    })

    it('does NOT call useCreateNode in edit mode', async () => {
      const user = userEvent.setup()
      const createMutate = vi.fn()
      mockedUseCreateNode.mockReturnValue(asCreateMutation(makeMutation({ mutate: createMutate })))
      mockedUseUpdateNode.mockReturnValue(asUpdateMutation(makeMutation()))

      renderEditDialog(makeNode())

      await user.click(screen.getByRole('button', { name: /save/i }))

      expect(createMutate).not.toHaveBeenCalled()
    })

    it('calls onOpenChange(false) on successful update', async () => {
      const user = userEvent.setup()
      const onOpenChange = vi.fn()
      const mutate = vi.fn((_body: unknown, opts?: Record<string, unknown>) => {
        const cb = opts?.['onSuccess']
        if (typeof cb === 'function') cb()
      })
      mockedUseUpdateNode.mockReturnValue(asUpdateMutation(makeMutation({ mutate })))

      render(
        <NodeEditDialog
          engagementId={ENGAGEMENT_ID}
          open={true}
          onOpenChange={onOpenChange}
          node={makeNode()}
        />,
      )

      await user.click(screen.getByRole('button', { name: /save/i }))

      expect(onOpenChange).toHaveBeenCalledWith(false)
    })
  })

  // -------------------------------------------------------------------------
  // Server error display (422 / 409)
  // -------------------------------------------------------------------------

  describe('server error display', () => {
    it('shows mutation error message inline in create mode (422)', () => {
      const serverError = new Error('label: String should have at least 1 character')
      mockedUseCreateNode.mockReturnValue(asCreateMutation(makeMutation({ error: serverError })))

      renderCreateDialog()

      expect(screen.getByRole('alert')).toHaveTextContent(
        'label: String should have at least 1 character',
      )
    })

    it('shows mutation error message inline in edit mode (409)', () => {
      const serverError = new Error('Engagement is archived')
      mockedUseUpdateNode.mockReturnValue(asUpdateMutation(makeMutation({ error: serverError })))

      renderEditDialog(makeNode())

      expect(screen.getByRole('alert')).toHaveTextContent('Engagement is archived')
    })

    it('does not show an alert when there is no error', () => {
      mockedUseCreateNode.mockReturnValue(asCreateMutation(makeMutation({ error: null })))

      renderCreateDialog()

      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    })
  })

  // -------------------------------------------------------------------------
  // Cancel button
  // -------------------------------------------------------------------------

  describe('cancel button', () => {
    it('calls onOpenChange(false) when Cancel is clicked', async () => {
      const user = userEvent.setup()
      const { onOpenChange } = renderCreateDialog()

      await user.click(screen.getByRole('button', { name: /cancel/i }))

      expect(onOpenChange).toHaveBeenCalledWith(false)
    })
  })
})
