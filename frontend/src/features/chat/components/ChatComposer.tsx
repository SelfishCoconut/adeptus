import { useMemo, useState, type KeyboardEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import type { SendChatMessageResult } from '@/shared/api'
import { usePinStore } from '@/features/graph/store/pinStore'
import { useSendChatMessage } from '../api'

interface ChatComposerProps {
  engagementId: string
  /** When true, the engagement is read-only (§4): the composer is disabled. */
  archived: boolean
  /** When true, a turn is currently streaming: block a second send until it settles. */
  isStreaming: boolean
  /** Called with the POST result so the panel can stream the new assistant message. */
  onSent: (result: SendChatMessageResult) => void
}

/**
 * Bottom composer: a textarea + send button. The send is disabled while the input is
 * empty/whitespace, while a turn is streaming, while the POST is in flight, or when the
 * engagement is archived (with a hint). On success the input clears and the parent is
 * notified so it can open the streaming socket. Enter sends; Shift+Enter inserts a newline.
 */
export function ChatComposer({ engagementId, archived, isStreaming, onSent }: ChatComposerProps) {
  const [content, setContent] = useState('')
  const sendMutation = useSendChatMessage(engagementId)

  // Read the current pinned set (Slice-08 pinStore) at send time so it forms the §5.3
  // "always-included" union arm (§5.4). Select the raw map and derive to keep a stable
  // reference (a selector returning a fresh array would churn the store snapshot).
  const pinnedByEngagement = usePinStore((s) => s.pinnedByEngagement)
  const pinnedNodeIds = useMemo(
    () => pinnedByEngagement[engagementId] ?? [],
    [pinnedByEngagement, engagementId],
  )

  const trimmed = content.trim()
  const disabled = archived || isStreaming || sendMutation.isPending
  const canSend = trimmed.length > 0 && !disabled

  const submit = () => {
    if (!canSend) return
    // recent_node_ids = pinned ∪ last-selected (Decision 1). No node-selection surface is
    // wired into the composer in this slice, so the union reduces to the pinned set; the
    // server caps it to N and dedupes against the pinned arm.
    sendMutation.mutate(
      { content: trimmed, pinnedNodeIds, recentNodeIds: pinnedNodeIds },
      {
        onSuccess: (result) => {
          setContent('')
          onSent(result)
        },
      },
    )
  }

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <div className="border-t p-3">
      <div className="flex items-end gap-2">
        <Textarea
          aria-label="Message the AI"
          placeholder={archived ? 'Engagement is archived — read-only' : 'Send a message…'}
          value={content}
          disabled={archived}
          rows={2}
          onChange={(event) => setContent(event.target.value)}
          onKeyDown={onKeyDown}
          className="min-h-0 resize-none"
        />
        <Button type="button" onClick={submit} disabled={!canSend}>
          Send
        </Button>
      </div>
      {archived ? (
        <p className="mt-1 text-xs text-muted-foreground">
          This engagement is archived and read-only — existing chat stays browsable.
        </p>
      ) : null}
      {sendMutation.isError ? (
        <p role="alert" className="mt-1 text-xs text-destructive">
          Failed to send — please try again.
        </p>
      ) : null}
    </div>
  )
}
