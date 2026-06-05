import { describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useLowConfidenceThreshold } from './useLowConfidenceThreshold'

vi.mock('../api', async (importActual) => {
  const actual = await importActual<typeof import('../api')>()
  return { ...actual, useChatMessages: vi.fn() }
})

import { useChatMessages } from '../api'

const mockUseChatMessages = vi.mocked(useChatMessages)

function pageWithThreshold(threshold: number | undefined) {
  return {
    data: {
      pages: [{ items: [], next_cursor: null, low_confidence_threshold: threshold }],
      pageParams: [null],
    },
  } as never
}

describe('useLowConfidenceThreshold', () => {
  it('returns the backend-supplied threshold', () => {
    mockUseChatMessages.mockReturnValue(pageWithThreshold(50))
    const { result } = renderHook(() => useLowConfidenceThreshold('eng-1'))
    expect(result.current).toBe(50)
  })

  it('falls back to 70 before the chat query has loaded', () => {
    mockUseChatMessages.mockReturnValue({ data: undefined } as never)
    const { result } = renderHook(() => useLowConfidenceThreshold('eng-1'))
    expect(result.current).toBe(70)
  })

  it('falls back to 70 when no engagement is selected', () => {
    mockUseChatMessages.mockReturnValue({ data: undefined } as never)
    const { result } = renderHook(() => useLowConfidenceThreshold(undefined))
    expect(result.current).toBe(70)
  })
})
