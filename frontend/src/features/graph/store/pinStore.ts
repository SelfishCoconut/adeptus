// Pin store — the project's first Zustand store.
//
// Pins are ephemeral, per-user, client-side UI state (slice 08 resolved open
// question 1): a pin "tells the AI to weight a node heavily in subsequent
// reasoning" (§5.4) and is read at AI-turn time by a later slice (§5.3 / Slice
// 12). They are NOT shared graph truth, so they live in localStorage rather
// than the single-writer graph — no backend write path, no migration.
//
// State is keyed by engagementId so pins never leak across engagements
// (§17.1). `reconcile` prunes pins for nodes that no longer exist in the live
// graph (slice 08 Risk 3).
import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'

export interface PinState {
  /** Map of engagementId -> array of pinned node ids (array for JSON persistence). */
  pinnedByEngagement: Record<string, string[]>
  isPinned: (engagementId: string, nodeId: string) => boolean
  togglePin: (engagementId: string, nodeId: string) => void
  pinnedNodeIds: (engagementId: string) => string[]
  /** Drop pins for nodes no longer present in the live graph (call after each load). */
  reconcile: (engagementId: string, liveNodeIds: string[]) => void
}

/** localStorage key for the persisted pin state. */
export const PIN_STORAGE_KEY = 'adeptus-graph-pins'

export const usePinStore = create<PinState>()(
  persist(
    (set, get) => ({
      pinnedByEngagement: {},

      isPinned: (engagementId, nodeId) =>
        (get().pinnedByEngagement[engagementId] ?? []).includes(nodeId),

      pinnedNodeIds: (engagementId) => get().pinnedByEngagement[engagementId] ?? [],

      togglePin: (engagementId, nodeId) =>
        set((state) => {
          const current = state.pinnedByEngagement[engagementId] ?? []
          const next = current.includes(nodeId)
            ? current.filter((id) => id !== nodeId)
            : [...current, nodeId]
          return {
            pinnedByEngagement: {
              ...state.pinnedByEngagement,
              [engagementId]: next,
            },
          }
        }),

      reconcile: (engagementId, liveNodeIds) =>
        set((state) => {
          const current = state.pinnedByEngagement[engagementId]
          if (!current || current.length === 0) return state
          const live = new Set(liveNodeIds)
          const next = current.filter((id) => live.has(id))
          // No-op if nothing was pruned — avoids a needless re-render/write.
          if (next.length === current.length) return state
          return {
            pinnedByEngagement: {
              ...state.pinnedByEngagement,
              [engagementId]: next,
            },
          }
        }),
    }),
    {
      name: PIN_STORAGE_KEY,
      storage: createJSONStorage(() => localStorage),
      // Persist only the data, never the action functions.
      partialize: (state) => ({ pinnedByEngagement: state.pinnedByEngagement }),
    },
  ),
)
