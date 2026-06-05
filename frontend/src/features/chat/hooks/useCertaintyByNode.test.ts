import { describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import type { ChatMessage } from '@/shared/api'
import { usePinStore } from '@/features/graph/store/pinStore'
import { useCertaintyByNode } from './useCertaintyByNode'

// Mock only useChatMessages; keep the real flattenChatPages so the map-building logic runs.
vi.mock('../api', async (importActual) => {
  const actual = await importActual<typeof import('../api')>()
  return { ...actual, useChatMessages: vi.fn() }
})

import { useChatMessages } from '../api'

const ENGAGEMENT_ID = 'eng-1'
const mockUseChatMessages = vi.mocked(useChatMessages)

const assistant = (id: string, claims: ChatMessage['claims']): ChatMessage => ({
  id,
  engagement_id: ENGAGEMENT_ID,
  role: 'assistant',
  content: 'x',
  status: 'complete',
  created_at: '2026-01-01T00:00:00Z',
  claims,
})

/** Wrap a single oldest-first page in the InfiniteData shape useChatMessages returns. */
function pageOf(items: ChatMessage[]) {
  return { data: { pages: [{ items, next_cursor: null }], pageParams: [null] } } as never
}

describe('useCertaintyByNode', () => {
  it('maps each claimed node id to its certainty', () => {
    mockUseChatMessages.mockReturnValue(
      pageOf([
        assistant('a1', [
          { text: 'apache', certainty: 60, node_id: 'node-A' },
          { text: 'open port', certainty: 90, node_id: 'node-B' },
        ]),
      ]),
    )
    const { result } = renderHook(() => useCertaintyByNode(ENGAGEMENT_ID))
    expect(result.current.get('node-A')).toBe(60)
    expect(result.current.get('node-B')).toBe(90)
  })

  it('prefers the most-recent turn on a node-id conflict', () => {
    // Oldest-first: the later turn's certainty must win.
    mockUseChatMessages.mockReturnValue(
      pageOf([
        assistant('a1', [{ text: 'old', certainty: 30, node_id: 'node-A' }]),
        assistant('a2', [{ text: 'new', certainty: 85, node_id: 'node-A' }]),
      ]),
    )
    const { result } = renderHook(() => useCertaintyByNode(ENGAGEMENT_ID))
    expect(result.current.get('node-A')).toBe(85)
  })

  it('omits nodes that no claim references (no badge)', () => {
    mockUseChatMessages.mockReturnValue(
      pageOf([assistant('a1', [{ text: 'no node', certainty: 50, node_id: null }])]),
    )
    const { result } = renderHook(() => useCertaintyByNode(ENGAGEMENT_ID))
    expect(result.current.size).toBe(0)
    expect(result.current.get('node-A')).toBeUndefined()
  })

  it('returns an empty map when no engagement is selected', () => {
    mockUseChatMessages.mockReturnValue({ data: undefined } as never)
    const { result } = renderHook(() => useCertaintyByNode(undefined))
    expect(result.current.size).toBe(0)
  })

  it('never writes the graph pin store (read-only overlay, ADR-0001 / §8.2)', () => {
    mockUseChatMessages.mockReturnValue(
      pageOf([assistant('a1', [{ text: 'apache', certainty: 60, node_id: 'node-A' }])]),
    )
    const before = JSON.stringify(usePinStore.getState().pinnedByEngagement)
    renderHook(() => useCertaintyByNode(ENGAGEMENT_ID))
    const after = JSON.stringify(usePinStore.getState().pinnedByEngagement)
    expect(after).toBe(before)
  })
})
