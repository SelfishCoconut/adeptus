import { useState, type KeyboardEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import type { SendChatMessageResult } from '@/shared/api'
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

  const trimmed = content.trim()
  const disabled = archived || isStreaming || sendMutation.isPending
  const canSend = trimmed.length > 0 && !disabled

  const submit = () => {
    if (!canSend) return
    sendMutation.mutate(trimmed, {
      onSuccess: (result) => {
        setContent('')
        onSent(result)
      },
    })
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
