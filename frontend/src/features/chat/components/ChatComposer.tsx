import { useEffect, useMemo, useState, type KeyboardEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import type { PrivacyMode, SendChatMessageResult } from '@/shared/api'
import { usePinStore } from '@/features/graph/store/pinStore'
import { usePersonas } from '@/features/personas/api'
import { usePersonaSelectionStore } from '@/features/personas/store'
import { PersonaSwitcher } from '@/features/personas/components/PersonaSwitcher'
import { ManagePersonasPanel } from '@/features/personas/components/ManagePersonasPanel'
import { EgressConfirmationRequiredError, useSendChatMessage } from '../api'
import { scanEgress } from '../egressScan'
import { EgressConfirmModal } from './EgressConfirmModal'

// Must match the backend's default-persona slug (personas/seed.py: GENERAL_SLUG). Renaming
// it there without updating this would silently break the general-default selection.
const GENERAL_SLUG = 'general'

interface ChatComposerProps {
  engagementId: string
  /** When true, the engagement is read-only (§4): the composer is disabled. */
  archived: boolean
  /** The engagement's privacy mode (§5.1). Only cloud_enabled sends are egress-scanned. */
  privacyMode: PrivacyMode
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
 *
 * Cloud egress friction (§5.1, Slice 14): on a cloud_enabled engagement the trimmed text is
 * pre-flight-scanned (egressScan) before the POST; if it matches a likely-secret pattern the
 * EgressConfirmModal is shown and the message is only sent (with confirmedEgress) on "Send
 * anyway" — Cancel keeps the composer text. The server re-scan is authoritative: a 409 the
 * client missed (drift) re-opens the modal from the server's categories (Risk 3). On a
 * local_only engagement nothing is scanned and the send is direct.
 */
export function ChatComposer({
  engagementId,
  archived,
  privacyMode,
  isStreaming,
  onSent,
}: ChatComposerProps) {
  const [content, setContent] = useState('')
  // Non-null while the friction modal is open: the matched category NAMES to display.
  const [pendingCategories, setPendingCategories] = useState<string[] | null>(null)
  const [manageOpen, setManageOpen] = useState(false)
  const sendMutation = useSendChatMessage(engagementId)

  // Personas (§5.3, Slice 15): the switcher's current selection rides on the POST body per
  // send, so switching takes effect on the next turn with no conversation reset. The library
  // is per-user (not engagement-scoped); the ephemeral selection is keyed by engagement and
  // defaults to the `general` built-in.
  const personasQuery = usePersonas()
  const personas = useMemo(() => personasQuery.data?.items ?? [], [personasQuery.data])
  const generalId = useMemo(
    () => personas.find((p) => p.slug === GENERAL_SLUG)?.id ?? null,
    [personas],
  )
  const storedPersonaId = usePersonaSelectionStore((s) => s.selectedByEngagement[engagementId])
  const selectPersona = usePersonaSelectionStore((s) => s.select)
  const reconcilePersonas = usePersonaSelectionStore((s) => s.reconcile)
  const selectedPersonaId = storedPersonaId ?? generalId ?? undefined

  // Drop a stale selection (its persona was deleted) so the switcher falls back to general.
  useEffect(() => {
    if (personas.length > 0) {
      reconcilePersonas(
        engagementId,
        personas.map((p) => p.id),
      )
    }
  }, [personas, engagementId, reconcilePersonas])

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

  // Fire the POST. recent_node_ids = pinned ∪ last-selected (Slice-12 Decision 1); no
  // node-selection surface is wired into the composer yet, so the union reduces to the pinned
  // set (server caps + dedupes). confirmedEgress carries the friction acknowledgement (§5.1).
  const doSend = (confirmedEgress: boolean) => {
    sendMutation.mutate(
      {
        content: trimmed,
        pinnedNodeIds,
        recentNodeIds: pinnedNodeIds,
        confirmedEgress,
        personaId: selectedPersonaId,
      },
      {
        onSuccess: (result) => {
          setContent('')
          setPendingCategories(null)
          onSent(result)
        },
        onError: (error) => {
          // Defense in depth: the client pre-flight missed a pattern the server caught — open
          // the modal from the server's categories so the user can confirm + retry (Risk 3).
          if (error instanceof EgressConfirmationRequiredError) {
            setPendingCategories(error.categories)
          }
        },
      },
    )
  }

  const submit = () => {
    if (!canSend) return
    // Cloud egress friction (§5.1): scan before the POST and gate behind the modal on a match.
    // local_only is never scanned (no egress to gate, §5.5).
    if (privacyMode === 'cloud_enabled') {
      const categories = scanEgress(trimmed)
      if (categories.length > 0) {
        setPendingCategories(categories)
        return
      }
    }
    doSend(false)
  }

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <div className="border-t p-3">
      {personas.length > 0 && selectedPersonaId ? (
        <div className="mb-2">
          <PersonaSwitcher
            personas={personas}
            selectedId={selectedPersonaId}
            onChange={(id) => selectPersona(engagementId, id)}
            onManage={() => setManageOpen(true)}
            disabled={disabled}
          />
        </div>
      ) : null}
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
      <ManagePersonasPanel open={manageOpen} onOpenChange={setManageOpen} />
      {archived ? (
        <p className="mt-1 text-xs text-muted-foreground">
          This engagement is archived and read-only — existing chat stays browsable.
        </p>
      ) : null}
      {sendMutation.isError && pendingCategories === null ? (
        <p role="alert" className="mt-1 text-xs text-destructive">
          Failed to send — please try again.
        </p>
      ) : null}
      <EgressConfirmModal
        open={pendingCategories !== null}
        categories={pendingCategories ?? []}
        onConfirm={() => doSend(true)}
        onCancel={() => setPendingCategories(null)}
      />
    </div>
  )
}
