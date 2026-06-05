"""Data-access layer for chat_messages (Slice 11).

All reads are scoped to ``(engagement_id, user_id)`` — the per-user privacy boundary
(§5.4 / §17.1). The service layer is responsible for the membership chokepoint before
calling any of these; this layer assumes the caller is already authorized for the
``user_id`` it passes.

Ordering note: a turn persists the user row and the pending-assistant row in ONE
transaction, where Postgres ``now()`` would assign both rows an identical
transaction-start timestamp. To guarantee the user message always sorts before its
assistant reply, ``insert_user_and_pending_assistant`` assigns explicit ``created_at``
values one microsecond apart. All ordering/keyset paths use ``(created_at, id)`` so the
random-UUID ``id`` is only a defensive tiebreaker.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.chat.models import ChatMessage


async def insert_user_and_pending_assistant(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    user_id: UUID,
    content: str,
) -> tuple[ChatMessage, ChatMessage]:
    """Insert the user message and an empty ``pending`` assistant placeholder in one
    flush (persist-first). Returns ``(user_message, assistant_message)``.

    Both rows are durable before any model work begins, so a dropped socket or a crash
    mid-stream leaves a recoverable ``pending`` row rather than a lost message. The
    assistant ``created_at`` is one microsecond after the user's so the pair has a
    stable, deterministic order. The caller commits.
    """
    now = datetime.now(UTC)
    user_message = ChatMessage(
        engagement_id=engagement_id,
        user_id=user_id,
        role="user",
        content=content,
        status="complete",
        created_at=now,
    )
    assistant_message = ChatMessage(
        engagement_id=engagement_id,
        user_id=user_id,
        role="assistant",
        content="",
        status="pending",
        created_at=now + timedelta(microseconds=1),
    )
    db.add_all([user_message, assistant_message])
    await db.flush()
    return user_message, assistant_message


async def get_message_for_owner(
    db: AsyncSession,
    *,
    message_id: UUID,
    user_id: UUID,
) -> ChatMessage | None:
    """Return the message only if it is owned by ``user_id`` (§5.4 ownership).

    Returns ``None`` for a missing message OR one owned by another user — the caller
    collapses both into a single WS close code (no existence disclosure).
    """
    result = await db.execute(
        select(ChatMessage).where(
            ChatMessage.id == message_id,
            ChatMessage.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def recent_messages(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    user_id: UUID,
    limit: int,
) -> list[ChatMessage]:
    """Return the last ``limit`` messages of the conversation, oldest-first.

    The window backing the prompt (§5.4 recent-messages-verbatim). Fetched newest-first
    with a bound, then reversed to oldest→newest so the service can map it straight into
    the Ollama ``messages`` array.
    """
    result = await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.engagement_id == engagement_id,
            ChatMessage.user_id == user_id,
        )
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(limit)
    )
    rows = list(result.scalars().all())
    rows.reverse()
    return rows


async def list_conversation(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    user_id: UUID,
    cursor: tuple[datetime, UUID] | None,
    limit: int,
) -> tuple[list[ChatMessage], tuple[datetime, UUID] | None]:
    """Return one page of the conversation oldest-first, plus the next (older) cursor.

    The first page is the most recent ``limit`` messages (ordered oldest-first within
    the page so the UI renders them top-to-bottom). A non-null ``next_cursor`` points at
    the batch of older messages preceding this page (infinite-scroll-up). Keyset on
    ``(created_at, id)``.
    """
    stmt = select(ChatMessage).where(
        ChatMessage.engagement_id == engagement_id,
        ChatMessage.user_id == user_id,
    )
    if cursor is not None:
        c_ts, c_id = cursor
        # Strictly-older keyset: rows that come before the cursor in DESC order.
        stmt = stmt.where(
            or_(
                ChatMessage.created_at < c_ts,
                and_(ChatMessage.created_at == c_ts, ChatMessage.id < c_id),
            )
        )
    stmt = stmt.order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(limit + 1)

    rows = list((await db.execute(stmt)).scalars().all())  # newest-first

    has_more = len(rows) > limit
    page = rows[:limit]  # newest-first, the most recent `limit` rows in range

    next_cursor: tuple[datetime, UUID] | None = None
    if has_more and page:
        oldest = page[-1]
        next_cursor = (oldest.created_at, cast(UUID, oldest.id))

    page.reverse()  # oldest-first for display
    return page, next_cursor


async def finalize_assistant(
    db: AsyncSession,
    *,
    message_id: UUID,
    content: str,
    status: str,
    model: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> ChatMessage | None:
    """Transition a ``pending`` assistant row to its terminal state.

    Sets the final ``content`` + ``status`` (``complete`` or ``failed``) and the
    model/token metadata. Returns the refreshed row, or ``None`` if it no longer exists.
    The caller commits (atomically with the ``ai_call`` audit entry).
    """
    result = await db.execute(select(ChatMessage).where(ChatMessage.id == message_id))
    message = result.scalar_one_or_none()
    if message is None:
        return None

    message.content = content
    message.status = status
    message.model = model
    message.prompt_tokens = prompt_tokens
    message.completion_tokens = completion_tokens
    await db.flush()
    return message
