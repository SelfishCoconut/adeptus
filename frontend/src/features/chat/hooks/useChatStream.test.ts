import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useChatStream } from './useChatStream'

// ---------------------------------------------------------------------------
// Fake WebSocket — records instances and lets tests drive messages / close.
// ---------------------------------------------------------------------------

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  static OPEN = 1
  static CLOSED = 3

  url: string
  readyState = 0
  onmessage: ((event: MessageEvent) => void) | null = null
  close = vi.fn(() => {
    this.readyState = FakeWebSocket.CLOSED
  })

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  emit(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent)
  }

  emitRaw(data: string) {
    this.onmessage?.({ data } as MessageEvent)
  }
}

const MESSAGE_ID = '00000000-0000-0000-0000-000000000042'

beforeEach(() => {
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useChatStream', () => {
  it('does not open a socket when the id is null', () => {
    const { result } = renderHook(() => useChatStream(null))
    expect(FakeWebSocket.instances).toHaveLength(0)
    expect(result.current).toEqual({ text: '', isDone: false, error: null, plan: [], claims: [] })
  })

  it('opens a ws:// socket targeting the message id', () => {
    renderHook(() => useChatStream(MESSAGE_ID))
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(FakeWebSocket.instances[0].url).toBe(`ws://localhost:3000/ws/chat/${MESSAGE_ID}`)
  })

  it('accumulates token frames into the text buffer', () => {
    const { result } = renderHook(() => useChatStream(MESSAGE_ID))
    const socket = FakeWebSocket.instances[0]

    act(() => {
      socket.emit({ type: 'token', data: 'Hel' })
      socket.emit({ type: 'token', data: 'lo ' })
      socket.emit({ type: 'token', data: 'world' })
    })

    expect(result.current.text).toBe('Hello world')
    expect(result.current.isDone).toBe(false)
    expect(result.current.error).toBeNull()
  })

  it('marks done and closes the socket on a done frame', () => {
    const { result } = renderHook(() => useChatStream(MESSAGE_ID))
    const socket = FakeWebSocket.instances[0]

    act(() => {
      socket.emit({ type: 'token', data: 'hi' })
      socket.emit({ type: 'done' })
    })

    expect(result.current.isDone).toBe(true)
    expect(result.current.text).toBe('hi')
    expect(socket.close).toHaveBeenCalled()
  })

  it('surfaces the plan and claims from the done frame', () => {
    const { result } = renderHook(() => useChatStream(MESSAGE_ID))
    const socket = FakeWebSocket.instances[0]

    act(() => {
      socket.emit({ type: 'token', data: 'Answer.' })
      socket.emit({
        type: 'done',
        plan: [
          { step: 'Enumerate login', status: 'done' },
          { step: 'Test SQLi', status: 'in_progress' },
        ],
        claims: [{ text: 'likely Apache', certainty: 60, node_id: null }],
      })
    })

    expect(result.current.isDone).toBe(true)
    expect(result.current.text).toBe('Answer.')
    expect(result.current.plan).toEqual([
      { step: 'Enumerate login', status: 'done' },
      { step: 'Test SQLi', status: 'in_progress' },
    ])
    expect(result.current.claims).toHaveLength(1)
    expect(result.current.claims[0].certainty).toBe(60)
  })

  it('leaves plan and claims empty when the done frame omits them', () => {
    const { result } = renderHook(() => useChatStream(MESSAGE_ID))
    const socket = FakeWebSocket.instances[0]

    act(() => {
      socket.emit({ type: 'token', data: 'plain' })
      socket.emit({ type: 'done' })
    })

    expect(result.current.isDone).toBe(true)
    expect(result.current.plan).toEqual([])
    expect(result.current.claims).toEqual([])
  })

  it('surfaces a stable error reason on an error frame', () => {
    const { result } = renderHook(() => useChatStream(MESSAGE_ID))
    const socket = FakeWebSocket.instances[0]

    act(() => {
      socket.emit({ type: 'error', message: 'AI is unreachable — local model is offline' })
    })

    expect(result.current.error).toBe('AI is unreachable — local model is offline')
    expect(result.current.isDone).toBe(true)
    expect(socket.close).toHaveBeenCalled()
  })

  it('ignores malformed frames', () => {
    const { result } = renderHook(() => useChatStream(MESSAGE_ID))
    const socket = FakeWebSocket.instances[0]

    act(() => {
      socket.emitRaw('not-json')
    })

    expect(result.current.text).toBe('')
    expect(result.current.isDone).toBe(false)
  })

  it('resets the buffer and reconnects when the id changes', () => {
    const { result, rerender } = renderHook(({ id }) => useChatStream(id), {
      initialProps: { id: MESSAGE_ID as string | null },
    })
    act(() => {
      FakeWebSocket.instances[0].emit({ type: 'token', data: 'first' })
    })
    expect(result.current.text).toBe('first')

    const nextId = '00000000-0000-0000-0000-000000000099'
    rerender({ id: nextId })

    expect(result.current.text).toBe('')
    expect(result.current.isDone).toBe(false)
    expect(FakeWebSocket.instances).toHaveLength(2)
    expect(FakeWebSocket.instances[1].url).toBe(`ws://localhost:3000/ws/chat/${nextId}`)
  })

  it('closes the socket on unmount', () => {
    const { unmount } = renderHook(() => useChatStream(MESSAGE_ID))
    const socket = FakeWebSocket.instances[0]
    unmount()
    expect(socket.close).toHaveBeenCalled()
  })
})
