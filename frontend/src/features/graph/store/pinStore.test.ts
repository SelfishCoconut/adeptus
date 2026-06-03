import { beforeEach, describe, expect, it } from 'vitest'
import { usePinStore, PIN_STORAGE_KEY } from './pinStore'

const ENG_A = 'engagement-a'
const ENG_B = 'engagement-b'

// Reset store + persisted storage between tests so cases don't bleed.
beforeEach(() => {
  localStorage.clear()
  usePinStore.setState({ pinnedByEngagement: {} })
})

describe('pinStore', () => {
  it('test_toggle_pin_adds_and_removes', () => {
    const { togglePin, isPinned } = usePinStore.getState()

    expect(isPinned(ENG_A, 'n1')).toBe(false)

    togglePin(ENG_A, 'n1')
    expect(usePinStore.getState().isPinned(ENG_A, 'n1')).toBe(true)

    togglePin(ENG_A, 'n1')
    expect(usePinStore.getState().isPinned(ENG_A, 'n1')).toBe(false)
  })

  it('pinnedNodeIds returns the pinned ids for an engagement', () => {
    const { togglePin } = usePinStore.getState()
    togglePin(ENG_A, 'n1')
    togglePin(ENG_A, 'n2')

    expect(usePinStore.getState().pinnedNodeIds(ENG_A).sort()).toEqual(['n1', 'n2'])
    expect(usePinStore.getState().pinnedNodeIds(ENG_B)).toEqual([])
  })

  it('test_pins_are_scoped_per_engagement', () => {
    const { togglePin } = usePinStore.getState()
    togglePin(ENG_A, 'shared-id')

    const state = usePinStore.getState()
    expect(state.isPinned(ENG_A, 'shared-id')).toBe(true)
    // Same node id under a different engagement must NOT be pinned.
    expect(state.isPinned(ENG_B, 'shared-id')).toBe(false)
  })

  it('test_pins_persist_via_localStorage', () => {
    usePinStore.getState().togglePin(ENG_A, 'n1')

    const raw = localStorage.getItem(PIN_STORAGE_KEY)
    expect(raw).not.toBeNull()
    const parsed = JSON.parse(raw as string) as {
      state: { pinnedByEngagement: Record<string, string[]> }
    }
    expect(parsed.state.pinnedByEngagement[ENG_A]).toContain('n1')
  })

  it('test_reconcile_drops_pins_for_vanished_nodes', () => {
    const { togglePin } = usePinStore.getState()
    togglePin(ENG_A, 'n1')
    togglePin(ENG_A, 'n2')
    togglePin(ENG_A, 'n3')

    // n2 no longer exists in the live graph.
    usePinStore.getState().reconcile(ENG_A, ['n1', 'n3'])

    expect(usePinStore.getState().pinnedNodeIds(ENG_A).sort()).toEqual(['n1', 'n3'])
  })

  it('reconcile leaves other engagements untouched', () => {
    const { togglePin } = usePinStore.getState()
    togglePin(ENG_A, 'a1')
    togglePin(ENG_B, 'b1')

    usePinStore.getState().reconcile(ENG_A, []) // wipe A's pins

    expect(usePinStore.getState().pinnedNodeIds(ENG_A)).toEqual([])
    expect(usePinStore.getState().pinnedNodeIds(ENG_B)).toEqual(['b1'])
  })

  it('reconcile is a no-op when nothing changed', () => {
    const { togglePin } = usePinStore.getState()
    togglePin(ENG_A, 'n1')
    const before = usePinStore.getState().pinnedByEngagement

    usePinStore.getState().reconcile(ENG_A, ['n1', 'n2'])

    // Same reference — no state churn when no pin was pruned.
    expect(usePinStore.getState().pinnedByEngagement).toBe(before)
  })
})
