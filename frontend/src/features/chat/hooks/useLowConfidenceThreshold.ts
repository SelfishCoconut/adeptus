import { useChatMessages } from '../api'
import { LOW_CONFIDENCE_THRESHOLD } from '../components/CertaintyBadge'

/**
 * Read the §5.3 low-confidence threshold from the backend (the single tunable
 * `ADEPTUS_CHAT_LOW_CONFIDENCE_THRESHOLD`, surfaced on `ChatMessagePage`). Falls back to the
 * frontend default constant until the chat query has loaded (or when no engagement is
 * selected). Cheap — it reads the already-cached chat query, deduped with the chat pane.
 */
export function useLowConfidenceThreshold(engagementId: string | undefined): number {
  const { data } = useChatMessages(engagementId ?? '', { enabled: Boolean(engagementId) })
  return data?.pages[0]?.low_confidence_threshold ?? LOW_CONFIDENCE_THRESHOLD
}
