// Persona-selection store — ephemeral, per-engagement client state (Slice 15).
//
// The composer's current persona is chosen PER SEND (it rides on the POST body, like
// confirmed_egress); the selection itself is ephemeral UI state, NOT persisted server-side
// and NOT remembered across reloads (slice 15 scope — last-used-persona persistence is a
// deferred polish). So, unlike the Slice-08 pin store, this store has no `persist`
// middleware: a reload resets every engagement back to the `general` default.
//
// Keyed by engagementId so a selection never leaks across engagements (§17.1). The store
// holds only EXPLICIT selections; the default (general) is supplied by the caller (derived
// from the loaded personas list) so the store stays decoupled from the API.
import { create } from 'zustand'

export interface PersonaSelectionState {
  /** engagementId -> explicitly-selected personaId. Absent ⇒ use the general default. */
  selectedByEngagement: Record<string, string>
  /** The selected persona for an engagement, or `generalId` when none is explicitly chosen. */
  selectedPersonaId: (engagementId: string, generalId: string) => string
  /** Explicitly select a persona for an engagement (the switcher onChange). */
  select: (engagementId: string, personaId: string) => void
  /**
   * Drop the engagement's selection if it is no longer one of `validIds` — e.g. the selected
   * custom persona was deleted — so the switcher falls back to general. Call after the
   * personas list loads/changes. No-op when the selection is still valid (stable reference).
   */
  reconcile: (engagementId: string, validIds: string[]) => void
}

export const usePersonaSelectionStore = create<PersonaSelectionState>()((set, get) => ({
  selectedByEngagement: {},

  selectedPersonaId: (engagementId, generalId) =>
    get().selectedByEngagement[engagementId] ?? generalId,

  select: (engagementId, personaId) =>
    set((state) => ({
      selectedByEngagement: { ...state.selectedByEngagement, [engagementId]: personaId },
    })),

  reconcile: (engagementId, validIds) =>
    set((state) => {
      const current = state.selectedByEngagement[engagementId]
      if (current === undefined || validIds.includes(current)) return state
      const next = { ...state.selectedByEngagement }
      delete next[engagementId]
      return { selectedByEngagement: next }
    }),
}))
