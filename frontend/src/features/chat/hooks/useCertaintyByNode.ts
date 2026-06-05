import { useMemo } from 'react'
import { flattenChatPages, useChatMessages } from '../api'

/**
 * Build a `node_id -> latest certainty` map from the caller's own loaded assistant turns
 * (§5.3 "certainty … on graph items"). This is a READ-ONLY overlay derived from the chat:
 * it never mutates the graph store, never goes through the single writer, and never writes
 * any `graph_*` table (§8.2 / ADR-0001). Certainty lives on the chat turn that asserted it,
 * not on the node — so the badge disappears on logout/reload exactly as the chat does.
 *
 * Per-user (§5.4): it reads the caller's own conversation only. The server already dropped
 * any foreign/unknown `node_id` at finalize (§17.1), so every id here is a live node of
 * this engagement. On conflict the most-recent turn wins (iterating oldest→newest and
 * overwriting). An empty/absent engagement yields an empty map (the query is disabled).
 */
export function useCertaintyByNode(engagementId: string | undefined): Map<string, number> {
  const { data } = useChatMessages(engagementId ?? '', { enabled: Boolean(engagementId) })

  return useMemo(() => {
    const byNode = new Map<string, number>()
    if (!engagementId) return byNode
    // flattenChatPages is oldest-first, so later (more recent) writes overwrite earlier
    // ones — the most-recent turn's certainty wins for a node referenced across turns.
    for (const message of flattenChatPages(data)) {
      if (message.role !== 'assistant') continue
      for (const claim of message.claims ?? []) {
        if (claim.node_id) byNode.set(claim.node_id, claim.certainty)
      }
    }
    return byNode
  }, [engagementId, data])
}
