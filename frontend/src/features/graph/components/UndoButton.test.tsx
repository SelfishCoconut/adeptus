import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { UndoButton } from './UndoButton'
import { usePopUndoStack, useUndoStack } from '../api'
import type { UndoResult, UndoStack, UndoStackEntry } from '../api'

vi.mock('../api', () => ({
  useUndoStack: vi.fn(),
  usePopUndoStack: vi.fn(),
}))

const mockedUseUndoStack = vi.mocked(useUndoStack)
const mockedUsePopUndoStack = vi.mocked(usePopUndoStack)

const ENGAGEMENT_ID = '00000000-0000-0000-0000-000000000001'

const ENTRY: UndoStackEntry = {
  id: '00000000-0000-0000-0000-000000000010',
  op_type: 'create_node',
  entity_kind: 'node',
  entity_id: '00000000-0000-0000-0000-000000000002',
  summary: 'Created host 10.0.0.5',
  recorded_at: '2026-01-01T00:00:00Z',
  stale: false,
}

function stackResult(depth: number): ReturnType<typeof useUndoStack> {
  const data: UndoStack = {
    depth,
    entries: Array.from({ length: depth }, () => ENTRY),
  }
  return { data } as unknown as ReturnType<typeof useUndoStack>
}

function popResult(
  overrides: Partial<{ mutateAsync: ReturnType<typeof vi.fn>; isPending: boolean }> = {},
): ReturnType<typeof usePopUndoStack> {
  return {
    mutate: vi.fn(),
    mutateAsync: overrides.mutateAsync ?? vi.fn(),
    isPending: overrides.isPending ?? false,
    error: null,
  } as unknown as ReturnType<typeof usePopUndoStack>
}

function makeUndoResult(over: Partial<UndoResult> = {}): UndoResult {
  return {
    undone: ENTRY,
    skipped_stale: [],
    stack: { depth: 0, entries: [] },
    ...over,
  }
}

beforeEach(() => {
  mockedUseUndoStack.mockReset()
  mockedUsePopUndoStack.mockReset()
})

describe('UndoButton', () => {
  it('renders the stack depth in the label', () => {
    mockedUseUndoStack.mockReturnValue(stackResult(3))
    mockedUsePopUndoStack.mockReturnValue(popResult())
    render(<UndoButton engagementId={ENGAGEMENT_ID} />)
    expect(screen.getByRole('button', { name: 'Undo my last change' })).toHaveTextContent('Undo (3)')
  })

  it('is disabled when the stack is empty', () => {
    mockedUseUndoStack.mockReturnValue(stackResult(0))
    mockedUsePopUndoStack.mockReturnValue(popResult())
    render(<UndoButton engagementId={ENGAGEMENT_ID} />)
    expect(screen.getByRole('button', { name: 'Undo my last change' })).toBeDisabled()
  })

  it('fires the pop mutation on click', async () => {
    const mutateAsync = vi.fn().mockResolvedValue(makeUndoResult())
    mockedUseUndoStack.mockReturnValue(stackResult(1))
    mockedUsePopUndoStack.mockReturnValue(popResult({ mutateAsync }))
    render(<UndoButton engagementId={ENGAGEMENT_ID} />)

    await userEvent.click(screen.getByRole('button', { name: 'Undo my last change' }))
    expect(mutateAsync).toHaveBeenCalledTimes(1)
  })

  it('shows the teammate-changed message when an entry was skipped as stale', async () => {
    const mutateAsync = vi
      .fn()
      .mockResolvedValue(makeUndoResult({ undone: null, skipped_stale: [{ ...ENTRY, stale: true }] }))
    mockedUseUndoStack.mockReturnValue(stackResult(1))
    mockedUsePopUndoStack.mockReturnValue(popResult({ mutateAsync }))
    render(<UndoButton engagementId={ENGAGEMENT_ID} />)

    await userEvent.click(screen.getByRole('button', { name: 'Undo my last change' }))
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent('a teammate changed this since'),
    )
  })

  it('does not show an error message when there was nothing to undo (undone === null)', async () => {
    const mutateAsync = vi.fn().mockResolvedValue(makeUndoResult({ undone: null }))
    // depth 1 so the button is enabled and the click is allowed.
    mockedUseUndoStack.mockReturnValue(stackResult(1))
    mockedUsePopUndoStack.mockReturnValue(popResult({ mutateAsync }))
    render(<UndoButton engagementId={ENGAGEMENT_ID} />)

    await userEvent.click(screen.getByRole('button', { name: 'Undo my last change' }))
    expect(mutateAsync).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('fires the pop on Ctrl+Z', async () => {
    const mutateAsync = vi.fn().mockResolvedValue(makeUndoResult())
    mockedUseUndoStack.mockReturnValue(stackResult(1))
    mockedUsePopUndoStack.mockReturnValue(popResult({ mutateAsync }))
    render(<UndoButton engagementId={ENGAGEMENT_ID} />)

    fireEvent.keyDown(window, { key: 'z', ctrlKey: true })
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1))
  })

  it('ignores Ctrl+Z while a dialog/input is focused (shortcutDisabled)', () => {
    const mutateAsync = vi.fn().mockResolvedValue(makeUndoResult())
    mockedUseUndoStack.mockReturnValue(stackResult(1))
    mockedUsePopUndoStack.mockReturnValue(popResult({ mutateAsync }))
    render(<UndoButton engagementId={ENGAGEMENT_ID} shortcutDisabled />)

    fireEvent.keyDown(window, { key: 'z', ctrlKey: true })
    expect(mutateAsync).not.toHaveBeenCalled()
  })
})
