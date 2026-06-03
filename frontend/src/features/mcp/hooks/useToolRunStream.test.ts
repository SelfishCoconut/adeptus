import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useToolRunStream } from './useToolRunStream'

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
  onclose: ((event: CloseEvent) => void) | null = null
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

const TOOL_RUN_ID = '00000000-0000-0000-0000-000000000042'

beforeEach(() => {
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
  // window.location.origin in jsdom is http://localhost:3000
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useToolRunStream', () => {
  it('does not open a socket when toolRunId is null', () => {
    const { result } = renderHook(() => useToolRunStream(null))
    expect(FakeWebSocket.instances).toHaveLength(0)
    expect(result.current).toEqual({ lines: [], isDone: false, exitCode: null, queued: false, queuePosition: null, queueReason: null })
  })

  it('opens a ws:// socket targeting the run id', () => {
    renderHook(() => useToolRunStream(TOOL_RUN_ID))
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(FakeWebSocket.instances[0].url).toBe(`ws://localhost:3000/ws/tool-runs/${TOOL_RUN_ID}`)
  })

  it('appends stdout and stderr chunks in order with their stream tag', () => {
    const { result } = renderHook(() => useToolRunStream(TOOL_RUN_ID))
    const socket = FakeWebSocket.instances[0]

    act(() => {
      socket.emit({ type: 'stdout', data: 'line one' })
      socket.emit({ type: 'stderr', data: 'oops' })
      socket.emit({ type: 'stdout', data: 'line two' })
    })

    expect(result.current.lines).toEqual([
      { stream: 'stdout', text: 'line one' },
      { stream: 'stderr', text: 'oops' },
      { stream: 'stdout', text: 'line two' },
    ])
    expect(result.current.isDone).toBe(false)
  })

  it('sets isDone and exitCode and closes the socket on done', () => {
    const { result } = renderHook(() => useToolRunStream(TOOL_RUN_ID))
    const socket = FakeWebSocket.instances[0]

    act(() => {
      socket.emit({ type: 'stdout', data: 'hi' })
      socket.emit({ type: 'done', exit_code: 0, finished_at: '2026-01-01T00:00:01Z' })
    })

    expect(result.current.isDone).toBe(true)
    expect(result.current.exitCode).toBe(0)
    expect(socket.close).toHaveBeenCalled()
  })

  it('reports a non-zero exit code', () => {
    const { result } = renderHook(() => useToolRunStream(TOOL_RUN_ID))
    act(() => {
      FakeWebSocket.instances[0].emit({ type: 'done', exit_code: 2 })
    })
    expect(result.current.exitCode).toBe(2)
  })

  it('pushes the error message as an stderr line and marks done', () => {
    const { result } = renderHook(() => useToolRunStream(TOOL_RUN_ID))
    act(() => {
      FakeWebSocket.instances[0].emit({ type: 'error', message: 'boom' })
    })

    expect(result.current.lines).toEqual([{ stream: 'stderr', text: 'boom' }])
    expect(result.current.isDone).toBe(true)
  })

  it('ignores unparseable messages', () => {
    const { result } = renderHook(() => useToolRunStream(TOOL_RUN_ID))
    act(() => {
      FakeWebSocket.instances[0].emitRaw('not json')
    })
    expect(result.current.lines).toEqual([])
  })

  it('closes the socket on unmount', () => {
    const { unmount } = renderHook(() => useToolRunStream(TOOL_RUN_ID))
    const socket = FakeWebSocket.instances[0]
    unmount()
    expect(socket.close).toHaveBeenCalled()
  })

  it('resets the buffer and opens a new socket when the run id changes', () => {
    const { result, rerender } = renderHook(({ id }) => useToolRunStream(id), {
      initialProps: { id: TOOL_RUN_ID as string | null },
    })
    act(() => {
      FakeWebSocket.instances[0].emit({ type: 'stdout', data: 'old run' })
    })
    expect(result.current.lines).toHaveLength(1)

    const nextId = '00000000-0000-0000-0000-000000000099'
    rerender({ id: nextId })

    expect(result.current.lines).toEqual([])
    expect(FakeWebSocket.instances).toHaveLength(2)
    expect(FakeWebSocket.instances[1].url).toContain(nextId)
  })

  // ---------------------------------------------------------------------------
  // Queued / started chunk handling (Slice 05 Task 10)
  // ---------------------------------------------------------------------------

  it('initial state has queued=false with no queue fields set', () => {
    const { result } = renderHook(() => useToolRunStream(TOOL_RUN_ID))
    expect(result.current.queued).toBe(false)
    expect(result.current.queuePosition).toBeNull()
    expect(result.current.queueReason).toBeNull()
  })

  it('queued→started→stdout sequence transitions state correctly', () => {
    const { result } = renderHook(() => useToolRunStream(TOOL_RUN_ID))
    const socket = FakeWebSocket.instances[0]

    // Step 1: receive queued
    act(() => {
      socket.emit({ type: 'queued', queue_position: 1, reason: 'slot_full' })
    })
    expect(result.current.queued).toBe(true)
    expect(result.current.queuePosition).toBe(1)
    expect(result.current.queueReason).toBe('slot_full')
    expect(result.current.lines).toEqual([])
    expect(result.current.isDone).toBe(false)

    // Step 2: receive started — queued fields clear
    act(() => {
      socket.emit({ type: 'started' })
    })
    expect(result.current.queued).toBe(false)
    expect(result.current.queuePosition).toBeNull()
    expect(result.current.queueReason).toBeNull()
    expect(result.current.isDone).toBe(false)

    // Step 3: stdout appends normally after admission
    act(() => {
      socket.emit({ type: 'stdout', data: 'result line' })
    })
    expect(result.current.lines).toEqual([{ stream: 'stdout', text: 'result line' }])
    expect(result.current.queued).toBe(false)
  })

  it('re-broadcast queued chunks update the position (and reason stays from first)', () => {
    const { result } = renderHook(() => useToolRunStream(TOOL_RUN_ID))
    const socket = FakeWebSocket.instances[0]

    // First queued message — position 2, reason set
    act(() => {
      socket.emit({ type: 'queued', queue_position: 2, reason: 'target_locked' })
    })
    expect(result.current.queuePosition).toBe(2)
    expect(result.current.queueReason).toBe('target_locked')

    // Second queued message — position shifts to 1 (no reason field in re-broadcast)
    act(() => {
      socket.emit({ type: 'queued', queue_position: 1 })
    })
    expect(result.current.queued).toBe(true)
    expect(result.current.queuePosition).toBe(1)
    // reason not present in the re-broadcast chunk, so it should not be overwritten
    expect(result.current.queueReason).toBe('target_locked')
  })

  it('a run that receives stdout with no preceding queued chunk stays queued=false', () => {
    const { result } = renderHook(() => useToolRunStream(TOOL_RUN_ID))
    const socket = FakeWebSocket.instances[0]

    act(() => {
      socket.emit({ type: 'stdout', data: 'immediate output' })
    })

    expect(result.current.queued).toBe(false)
    expect(result.current.queuePosition).toBeNull()
    expect(result.current.queueReason).toBeNull()
    expect(result.current.lines).toEqual([{ stream: 'stdout', text: 'immediate output' }])
  })

  it('queued chunk with target_locked reason sets the reason correctly', () => {
    const { result } = renderHook(() => useToolRunStream(TOOL_RUN_ID))
    const socket = FakeWebSocket.instances[0]

    act(() => {
      socket.emit({ type: 'queued', queue_position: 3, reason: 'target_locked' })
    })

    expect(result.current.queued).toBe(true)
    expect(result.current.queuePosition).toBe(3)
    expect(result.current.queueReason).toBe('target_locked')
  })
})
