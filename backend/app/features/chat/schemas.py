"""Pydantic v2 schemas for the chat feature (Slice 11).

The ``ChatRole`` / ``ChatMessageStatus`` StrEnums mirror ``models.CHAT_ROLES`` /
``models.CHAT_STATUSES`` exactly (guarded by test_schemas) so the API vocabulary and
the DB CHECK constraints can never silently drift.

``WebSocketChatChunk`` and ``OllamaChatMessage`` are internal value objects (the WS
frame contract is hand-mirrored in the frontend hook; the Ollama messages array is the
client's input) — neither is a response_model, so neither appears in the OpenAPI doc.
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ChatMessageCreate",
    "ChatMessagePage",
    "ChatMessageRead",
    "ChatMessageStatus",
    "ChatRole",
    "ChatTurnDebug",
    "Claim",
    "EgressConfirmationRequired",
    "EgressRefusalReason",
    "GraphSubsetEdge",
    "GraphSubsetNode",
    "GraphSubsetReason",
    "OllamaChatMessage",
    "PlanStep",
    "PlanStepStatus",
    "SendChatMessageResult",
    "WebSocketChatChunk",
]

# Hard cap on a single user message (also enforced by the API contract). Sent to the
# model verbatim — no redaction (§5.5).
MAX_MESSAGE_CHARS = 32768


class ChatRole(StrEnum):
    """Chat message author role. Mirrors ``models.CHAT_ROLES``."""

    USER = "user"
    ASSISTANT = "assistant"


class ChatMessageStatus(StrEnum):
    """Lifecycle of a chat message. Mirrors ``models.CHAT_STATUSES``.

    ``user`` messages are always ``complete``. An ``assistant`` message is ``pending``
    until its stream finishes (``complete``) or errors (``failed``).
    """

    COMPLETE = "complete"
    PENDING = "pending"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# §5.3 visible plan + uncertainty signaling (Slice 13)
# ---------------------------------------------------------------------------


class PlanStepStatus(StrEnum):
    """Lifecycle of one step in the AI's visible running plan (§5.3 visible plan).

    Unlike ``ChatRole``/``ChatMessageStatus`` this enum has NO backing DB column — plan
    steps live inside the per-turn ``chat_messages.graph_context`` JSONB blob, not their
    own table — so there is no DB-vocabulary parity to guard. ``in_progress`` mirrors the
    underscore wire form used in the ``<adeptus-meta>`` block (Design notes)."""

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class PlanStep(BaseModel):
    """One ordered todo-list item the AI is tracking this turn (§5.3 visible plan).

    Rendered verbatim in the inline Plan panel (no redaction, §5.5). Parsed server-side
    from the model's trailing ``<adeptus-meta>`` block; an absent/invalid ``status`` is
    coerced to ``todo`` by the parser before this model is constructed (task 2)."""

    step: str = Field(description="The todo-list item text, verbatim (§5.5).")
    status: PlanStepStatus = Field(description="The step's current state (todo/in_progress/done).")


class Claim(BaseModel):
    """One AI claim flagged with a stated certainty percentage (§5.3 uncertainty signaling).

    ``certainty`` is bounded 0–100 (the parser clamps out-of-range values before this model
    is constructed). ``node_id`` is the graph node the claim is about, if any — validated at
    finalize against the engagement's live graph (foreign/unknown ids dropped, §17.1) so the
    Graph-pane certainty badge never points at a foreign/hallucinated node. ``text`` is
    rendered verbatim (no redaction, §5.5)."""

    text: str = Field(description="The claim the AI flagged, verbatim (§5.5).")
    certainty: int = Field(
        ge=0, le=100, description="Stated certainty percentage for this claim (§5.3)."
    )
    node_id: UUID | None = Field(
        default=None,
        description=(
            "The graph node this claim is about, if any; validated against the engagement's "
            "live graph (foreign/unknown ids dropped, §17.1). Drives the Graph-pane badge."
        ),
    )


class ChatMessageCreate(BaseModel):
    """Request body for POST .../chat/messages.

    The three id lists carry the client-supplied arms of the §5.3 "relevant subset"
    union: ``pinned_node_ids`` (the always-included pinned set, §5.4), ``recent_node_ids``
    (the "last N touched in the conversation" approximation, most-recent-first), and
    ``mentioned_node_ids`` (the @-mention arm, empty until Slice 31). They are *inputs* only
    — the server re-resolves each id against the engagement's live graph (foreign/unknown
    ids are ignored, §17.1) and runs the keyword arm itself, so the client can never inject
    node content, only ids.
    """

    content: str = Field(
        min_length=1,
        max_length=MAX_MESSAGE_CHARS,
        description="The user's message text, sent verbatim to the model (no redaction, §5.5).",
    )
    pinned_node_ids: list[UUID] = Field(
        default_factory=list,
        description=(
            "Node ids the user has pinned (client-side pinStore, §5.4). Always-included "
            "arm of the §5.3 union. Unknown/foreign ids are ignored server-side (§17.1)."
        ),
    )
    recent_node_ids: list[UUID] = Field(
        default_factory=list,
        description=(
            "Node ids recently interacted with this session, most-recent-first; the "
            '"last N nodes touched in the conversation" arm (§5.3). Server truncates to N.'
        ),
    )
    mentioned_node_ids: list[UUID] = Field(
        default_factory=list,
        description=(
            "Node ids @-mentioned in recent messages (§5.3). Empty until the @-mention UI "
            "(Slice 31); accepted now for forward-compatibility. Server truncates to K."
        ),
    )
    confirmed_egress: bool = Field(
        default=False,
        description=(
            "The user has seen the cloud egress-friction modal for THIS content and chose to "
            "send it unmodified anyway (§5.1, Slice 14). Consulted only when the engagement is "
            "cloud_enabled and the content matched a secret pattern; ignored otherwise. Never "
            "suppresses the audit record (a confirmed send is audited as confirmed)."
        ),
    )
    persona_id: UUID | None = Field(
        default=None,
        description=(
            "The persona whose system prompt should shape THIS turn (§5.3, Slice 15). Must be a "
            "built-in or one of the caller's own personas; an unknown/foreign id falls back to "
            "the `general` built-in server-side (§17.1, never errors). Null/absent → `general`. "
            "Chosen per send so the user can switch persona mid-chat without resetting the "
            "conversation."
        ),
    )


class EgressRefusalReason(StrEnum):
    """Why a POST .../chat/messages was refused with 409 (Slice 14).

    The single POST 409 covers two cases with a distinguishable body: a cloud-enabled send
    that matched a likely-secret pattern without confirmation (§5.1 pattern-friction), and an
    archived engagement (§4 read-only). The reason lets the client tell them apart."""

    EGRESS_SECRET_FLAGGED = "egress_secret_flagged"
    ENGAGEMENT_ARCHIVED = "engagement_archived"


class EgressConfirmationRequired(BaseModel):
    """Body of the POST .../chat/messages 409 (Slice 14, §5.1).

    ``matched_categories`` are pattern category NAMES only (e.g. ``"aws_access_key"``) — NEVER
    the matched secret value (§5.5 / Risk 7) — for the friction modal's copy. Empty for the
    ``engagement_archived`` reason. A client re-sends with ``confirmed_egress=true`` to proceed
    past the friction case."""

    reason: EgressRefusalReason = Field(description="Why the POST was refused with 409.")
    matched_categories: list[str] = Field(
        default_factory=list,
        description=(
            "Names of the secret-pattern categories the content matched (§5.1). Empty for the "
            "archived reason. NEVER contains the matched value (§5.5) — only the category name."
        ),
    )


class ChatMessageRead(BaseModel):
    """One chat message as exposed by the read/write API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    engagement_id: UUID
    role: ChatRole
    content: str
    status: ChatMessageStatus
    created_at: datetime
    plan: list[PlanStep] = Field(
        default_factory=list,
        description=(
            "The AI's running plan as of this turn (§5.3). Empty for user/pending/pre-slice "
            "rows; populated from the assistant row's parsed metadata so a reloaded "
            "conversation re-renders the Plan panel without the debug call."
        ),
    )
    claims: list[Claim] = Field(
        default_factory=list,
        description=(
            "Certainty-tagged claims parsed from this turn (§5.3). Empty when none; drives "
            "the inline certainty badges and the Graph-pane overlay."
        ),
    )
    persona_id: UUID | None = Field(
        default=None,
        description=(
            "The persona used for this assistant turn (§5.3, Slice 15). Null for user/pending/"
            "pre-slice rows. A soft reference: the persona may since have been renamed/deleted."
        ),
    )
    persona_name: str | None = Field(
        default=None,
        description=(
            "The persona's display name at turn time, denormalized onto the turn so a renamed/"
            "deleted persona still labels the historical turn (the in-chat persona chip). Null "
            "for user/pending/pre-slice rows."
        ),
    )


class SendChatMessageResult(BaseModel):
    """Result of POST .../chat/messages: the persisted user message plus a ``pending``
    assistant placeholder. Stream the assistant reply via WS using its id."""

    user_message: ChatMessageRead
    assistant_message: ChatMessageRead


class ChatMessagePage(BaseModel):
    """A page of chat messages (oldest-first) with an opaque cursor for the next
    (older) page; ``next_cursor`` is null on the last page."""

    items: list[ChatMessageRead]
    next_cursor: str | None
    low_confidence_threshold: int = Field(
        default=70,
        ge=0,
        le=100,
        description=(
            "Certainty %% below which a claim renders as low-confidence (§5.3). This is the "
            "single backend tunable (ADEPTUS_CHAT_LOW_CONFIDENCE_THRESHOLD) surfaced to the "
            "UI so the frontend reads one source of truth, not a hard-coded mirror."
        ),
    )


class WebSocketChatChunk(BaseModel):
    """A single JSON frame sent over WS /ws/chat/{assistant_message_id}.

    type "token": ``data`` carries an incremental piece of assistant text (append).
    type "done":  the stream finished; the assistant message is persisted ``complete``.
    type "error": ``message`` carries a stable, non-leaky reason (e.g. the local model
                  being offline).
    """

    type: Literal["token", "done", "error"]
    data: str | None = None
    message: str | None = None
    # Slice 13: the "done" frame carries the parsed running plan + certainty claims for the
    # turn (may be empty). Withheld (None) on token/error frames; the token stream is the
    # block-stripped prose so the raw <adeptus-meta> block never reaches the client.
    plan: list[PlanStep] | None = None
    claims: list[Claim] | None = None


class OllamaChatMessage(BaseModel):
    """One entry of the Ollama ``messages`` array (system / user / assistant turn)."""

    role: Literal["system", "user", "assistant"]
    content: str


# ---------------------------------------------------------------------------
# AI debug panel (§14) — the per-turn "relevant subset" record (Slice 12)
# ---------------------------------------------------------------------------


class GraphSubsetReason(StrEnum):
    """Why a node entered the §5.3 relevant subset. A node may carry several reasons."""

    PINNED = "pinned"
    RECENT = "recent"
    MENTIONED = "mentioned"
    KEYWORD = "keyword"


class GraphSubsetNode(BaseModel):
    """One graph node included in the per-turn subset, tagged with its inclusion reason(s).

    Labels/properties are NOT carried here — only the rendered ``context_block`` (on
    ``ChatTurnDebug``) holds the verbatim text sent to the model (§5.5). This row is the
    grouping/affordance data the debug panel renders (id + type + label + reasons)."""

    id: UUID
    type: str
    label: str
    reasons: list[GraphSubsetReason] = Field(
        description="Why this node was included (one node may have several reasons)."
    )


class GraphSubsetEdge(BaseModel):
    """One edge between two selected nodes (only edges with both endpoints in the subset)."""

    id: UUID
    source_id: UUID
    target_id: UUID
    relation: str


class ChatTurnDebug(BaseModel):
    """The AI debug record for one assistant turn (§14): the exact relevant subset of the
    graph injected, the raw prompt, and the model output.

    Read by ``GET .../chat/messages/{message_id}/debug``. ``model_output`` is the assistant
    row's ``content`` (empty while pending/failed). ``nodes``/``edges``/``context_block``/
    ``raw_prompt`` are read back from the persisted ``graph_context`` JSONB; an assistant row
    that completed before this slice (or an empty-graph turn) has an empty subset and an
    empty ``context_block``."""

    message_id: UUID
    model: str | None
    status: ChatMessageStatus
    nodes: list[GraphSubsetNode]
    edges: list[GraphSubsetEdge]
    context_block: str = Field(
        description="The exact graph-context text prepended to the system prompt this turn."
    )
    raw_prompt: str = Field(description='The full prompt sent to the model (§14 "raw prompts").')
    model_output: str = Field(
        description=(
            "The model's raw output for this turn, INCLUDING the structured "
            "<adeptus-meta> block (§14 — the debug view shows the unstripped output so a "
            "power user can see exactly what was parsed). Empty while pending/failed."
        )
    )
    plan: list[PlanStep] = Field(
        default_factory=list,
        description="The plan parsed from this turn's metadata block (§5.3 / §14).",
    )
    claims: list[Claim] = Field(
        default_factory=list,
        description="The certainty claims parsed from this turn's metadata block (§5.3 / §14).",
    )
    persona_id: UUID | None = Field(
        default=None,
        description=(
            "The persona that shaped this turn (§5.3 / §17.6, Slice 15). Null for "
            "user/pending/pre-slice rows. The raw_prompt's leading system content is this "
            "persona's prompt, so the panel shows exactly which persona was used."
        ),
    )
    persona_name: str | None = Field(
        default=None,
        description="The persona's display name at turn time (§5.3). Null when no persona.",
    )
