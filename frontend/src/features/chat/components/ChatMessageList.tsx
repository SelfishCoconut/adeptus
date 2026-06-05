import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import type { ChatMessage, Claim, PlanStep } from '@/shared/api'
import { Badge } from '@/components/ui/badge'
import { AiDebugPanel } from './AiDebugPanel'
import { CertaintyBadge } from './CertaintyBadge'
import { PlanPanel } from './PlanPanel'

interface ChatMessageListProps {
  /** The engagement these messages belong to (for the per-turn debug panel). */
  engagementId: string
  /** The conversation so far, oldest-first. */
  messages: ChatMessage[]
  /** Id of the assistant message currently streaming, or null. */
  streamingId: string | null
  /** Accumulated tokens for the streaming message (live region). */
  streamingText: string
  /** Stable reason when the streaming turn failed/offline (§5.1), else null. */
  streamError: string | null
  /** The running plan from the just-finished stream's done frame (§5.3), empty otherwise. */
  streamingPlan?: PlanStep[]
  /** Low-confidence threshold for the in-chat certainty badges (backend tunable, §5.3). */
  threshold?: number
}

const OFFLINE_TEXT = 'AI is unreachable — local model is offline'

/** Inline failed/offline state for an assistant turn (§5.1). */
function OfflineNotice({ reason }: { reason: string }) {
  return (
    <p role="alert" className="text-sm text-destructive">
      {reason}
    </p>
  )
}

/** Inline certainty badges for an assistant turn's flagged claims (§5.3 "in chat"). */
function ClaimBadges({ claims, threshold }: { claims: Claim[]; threshold?: number }) {
  if (claims.length === 0) return null
  return (
    <div data-testid="claim-badges" className="flex flex-wrap gap-1.5">
      {claims.map((claim, index) => (
        <CertaintyBadge key={`${index}:${claim.text}`} claim={claim} threshold={threshold} />
      ))}
    </div>
  )
}

/** A user turn: right-aligned plain-text bubble (sent verbatim, never redacted). */
function UserRow({ message }: { message: ChatMessage }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[80%] whitespace-pre-wrap rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
        {message.content}
      </div>
    </div>
  )
}

/**
 * The persona that produced an assistant turn (§5.3, Slice 15): a small chip shown above
 * the reply so a reloaded conversation shows which persona shaped each turn. Omitted when
 * the turn has no persona (user/pre-slice rows, or a turn from before this slice).
 */
function PersonaChip({ name }: { name: string | null | undefined }) {
  if (!name) return null
  return (
    <Badge variant="secondary" className="text-[10px]" data-testid="persona-chip">
      {name}
    </Badge>
  )
}

/** An assistant turn: left-aligned bubble; Markdown when complete. */
function AssistantRow({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex justify-start">
      <div className="max-w-[80%] rounded-lg bg-muted px-3 py-2 text-sm text-foreground">
        {children}
      </div>
    </div>
  )
}

/**
 * A finalized assistant turn (complete or failed) with a small "Debug" affordance that
 * lazily opens the §14 AI debug panel for this turn. Optimistic/pending rows never reach
 * here — they have no persisted debug record until the stream finalizes.
 */
function FinalizedAssistantTurn({
  engagementId,
  messageId,
  isDebugOpen,
  onToggleDebug,
  plan,
  personaName,
  children,
}: {
  engagementId: string
  messageId: string
  isDebugOpen: boolean
  onToggleDebug: () => void
  /** When provided (latest turn only), the Plan panel renders above the reply (§5.3). */
  plan?: PlanStep[]
  /** The persona that produced this turn (§5.3, Slice 15); a chip above the reply. */
  personaName?: string | null
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col items-start gap-1">
      {plan !== undefined ? (
        <div className="w-full max-w-[80%]">
          <PlanPanel plan={plan} />
        </div>
      ) : null}
      <PersonaChip name={personaName} />
      <AssistantRow>{children}</AssistantRow>
      <button
        type="button"
        onClick={onToggleDebug}
        aria-expanded={isDebugOpen}
        className="px-1 text-xs text-muted-foreground hover:text-foreground"
      >
        {isDebugOpen ? 'Hide debug' : 'Debug'}
      </button>
      {isDebugOpen ? (
        <div className="w-full max-w-[80%]">
          <AiDebugPanel engagementId={engagementId} messageId={messageId} />
        </div>
      ) : null}
    </div>
  )
}

/**
 * Scrollable message list. Renders user vs assistant turns, the in-flight assistant
 * message as a streaming live region, completed assistant content as Markdown (§11.1),
 * and an inline failed/offline state for failed turns (§5.1). Auto-scrolls to the
 * newest message as content arrives.
 */
export function ChatMessageList({
  engagementId,
  messages,
  streamingId,
  streamingText,
  streamError,
  streamingPlan = [],
  threshold,
}: ChatMessageListProps) {
  const endRef = useRef<HTMLDivElement | null>(null)
  // The AI's plan is shown for the LATEST assistant turn only (the "running" plan, §5.3);
  // older turns keep their plan in the debug panel (task 13) but don't crowd the pane.
  const latestAssistantId = [...messages].reverse().find((m) => m.role === 'assistant')?.id ?? null
  // The single assistant turn whose debug panel is open (one at a time), or null.
  const [openDebugId, setOpenDebugId] = useState<string | null>(null)
  const toggleDebug = (id: string) =>
    setOpenDebugId((current) => (current === id ? null : id))

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [messages, streamingText, streamError])

  if (messages.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-4">
        <p className="text-sm text-muted-foreground">
          Ask the local AI about this engagement.
        </p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3 overflow-y-auto p-4" data-testid="chat-message-list">
      {messages.map((message) => {
        if (message.role === 'user') {
          return <UserRow key={message.id} message={message} />
        }

        // Treat the message as live only while it is still pending: once the refetched
        // row is complete/failed it renders as history (Markdown / offline notice), even
        // though streamingId may still point at it.
        const isStreaming = message.id === streamingId && message.status === 'pending'

        if (isStreaming && streamError) {
          return (
            <AssistantRow key={message.id}>
              <OfflineNotice reason={streamError} />
            </AssistantRow>
          )
        }

        if (isStreaming) {
          // The plan arrives on the stream's done frame (before the refetch finalizes the
          // row); show it above the still-streaming bubble the moment it lands (§5.3).
          return (
            <div key={message.id} className="flex flex-col items-start gap-1">
              {streamingPlan.length > 0 ? (
                <div className="w-full max-w-[80%]">
                  <PlanPanel plan={streamingPlan} />
                </div>
              ) : null}
              <AssistantRow>
                <div aria-live="polite" className="whitespace-pre-wrap">
                  {streamingText || <span className="text-muted-foreground">…</span>}
                </div>
              </AssistantRow>
            </div>
          )
        }

        if (message.status === 'failed') {
          return (
            <FinalizedAssistantTurn
              key={message.id}
              engagementId={engagementId}
              messageId={message.id}
              isDebugOpen={openDebugId === message.id}
              onToggleDebug={() => toggleDebug(message.id)}
              personaName={message.persona_name}
            >
              <OfflineNotice reason={OFFLINE_TEXT} />
            </FinalizedAssistantTurn>
          )
        }

        if (message.status === 'pending') {
          return (
            <AssistantRow key={message.id}>
              <span className="text-muted-foreground">…</span>
            </AssistantRow>
          )
        }

        return (
          <FinalizedAssistantTurn
            key={message.id}
            engagementId={engagementId}
            messageId={message.id}
            isDebugOpen={openDebugId === message.id}
            onToggleDebug={() => toggleDebug(message.id)}
            plan={message.id === latestAssistantId ? (message.plan ?? []) : undefined}
            personaName={message.persona_name}
          >
            <div className="space-y-2 [&_code]:rounded [&_code]:bg-background [&_code]:px-1">
              <ReactMarkdown>{message.content}</ReactMarkdown>
              <ClaimBadges claims={message.claims ?? []} threshold={threshold} />
            </div>
          </FinalizedAssistantTurn>
        )
      })}
      <div ref={endRef} />
    </div>
  )
}
