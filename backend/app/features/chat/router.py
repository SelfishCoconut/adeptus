"""FastAPI routes for the chat feature (Slice 11).

Endpoints:
  GET  /api/v1/engagements/{engagement_id}/chat/messages
      List the caller's own private conversation, oldest-first, paginated.
      Membership required (404 for non-members / missing engagement, §17.1).
  POST /api/v1/engagements/{engagement_id}/chat/messages
      Persist a user message + an empty pending assistant placeholder; the reply is
      streamed separately over the WebSocket. Membership required; 409 if archived (§4).
  WS   /ws/chat/{assistant_message_id}
      Stream the assistant reply token-by-token (or replay a completed/failed turn).
      Auth via session cookie on the upgrade; closes 4003 on ANY auth/authz failure.

Domain exceptions translate via the registered handlers (app.core.errors.handlers):
  NotFoundError            → 404  (engagement missing OR caller not a member, §17.1)
  EngagementArchivedError  → 409  (subclass of ConflictError; archived = read-only, §4)
"""

from contextlib import aclosing
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db, get_sessionmaker
from app.features.auth.deps import get_current_user
from app.features.auth.models import User
from app.features.chat import service
from app.features.chat.schemas import (
    ChatMessageCreate,
    ChatMessagePage,
    ChatTurnDebug,
    SendChatMessageResult,
)

router = APIRouter(tags=["chat"])

_WS_CLOSE_UNAUTH = 4003


@router.get(
    "/api/v1/engagements/{engagement_id}/chat/messages",
    response_model=ChatMessagePage,
    operation_id="list_chat_messages",
)
async def list_chat_messages(
    engagement_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ChatMessagePage:
    """List the calling user's private chat messages for an engagement, oldest-first.

    Returns only the caller's own conversation (§5.4 per-user privacy). Requires
    membership; a non-member or missing engagement returns 404 (no existence disclosure).
    """
    return await service.list_messages(
        db,
        engagement_id=engagement_id,
        requester=current_user,
        cursor=cursor,
        limit=limit,
    )


@router.post(
    "/api/v1/engagements/{engagement_id}/chat/messages",
    response_model=SendChatMessageResult,
    operation_id="send_chat_message",
    status_code=status.HTTP_201_CREATED,
)
async def send_chat_message(
    engagement_id: UUID,
    body: ChatMessageCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> SendChatMessageResult:
    """Persist a user message and an empty pending assistant message, then return both.

    Stream the assistant reply over ``WS /ws/chat/{assistant_message_id}``. Requires
    membership (404); an archived engagement is read-only (409, §4).
    """
    result = await service.send_message(
        db,
        engagement_id=engagement_id,
        requester=current_user,
        content=body.content,
        pinned_node_ids=body.pinned_node_ids,
        recent_node_ids=body.recent_node_ids,
        mentioned_node_ids=body.mentioned_node_ids,
    )
    await db.commit()
    return result


@router.get(
    "/api/v1/engagements/{engagement_id}/chat/messages/{message_id}/debug",
    response_model=ChatTurnDebug,
    operation_id="get_chat_turn_debug",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Not authenticated"},
        status.HTTP_404_NOT_FOUND: {
            "description": "Message not found, not owned by caller, or not an assistant turn"
        },
    },
)
async def get_chat_turn_debug(
    engagement_id: UUID,
    message_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChatTurnDebug:
    """Return the AI debug record (§14) for one of the caller's own assistant turns.

    The exact §5.3 relevant subset of the graph injected, the raw prompt, and the model
    output. Membership + ownership scoped: a non-member, non-owner, wrong-engagement, or
    non-assistant message all return 404 (no existence disclosure, §17.1 / §5.4).
    """
    return await service.get_turn_debug(
        db,
        engagement_id=engagement_id,
        requester=current_user,
        message_id=message_id,
    )


@router.websocket("/ws/chat/{assistant_message_id}")
async def stream_chat_ws(websocket: WebSocket, assistant_message_id: UUID) -> None:
    """Stream the assistant reply for a pending message (or replay a terminal one).

    Authentication is via the session cookie on the upgrade request (not
    get_current_user — that slides the session expiry and emits Set-Cookie, both
    inappropriate on a WS upgrade), mirroring the tool-run WS.

    Authorization: the caller must own the message AND be a member of its engagement.

    Close codes:
      4003 — any auth/authz failure (cookie missing/invalid/expired, message not found,
              not owned by caller, or caller not a member). One code, no disclosure.
      1000 — normal close after the stream completes (done/error sent).
    """
    session_id = websocket.cookies.get(get_settings().SESSION_COOKIE_NAME)
    async with get_sessionmaker()() as session:
        message = await service.authenticate_ws_chat_message(
            session, session_id=session_id, message_id=assistant_message_id
        )
    if message is None:
        await websocket.close(code=_WS_CLOSE_UNAUTH)
        return

    await websocket.accept()
    try:
        # aclosing guarantees the streaming generator is closed on EVERY exit path —
        # including a mid-stream client disconnect (send_json raises WebSocketDisconnect).
        # Closing it promptly runs the generator's `async with session` cleanup so the DB
        # session is released immediately rather than at GC. On a disconnect before
        # finalization the row simply stays `pending` and is re-streamed on reconnect
        # (Risk 2); after finalization the committed row is replayed on reconnect.
        async with aclosing(service.stream_assistant_reply(message=message)) as stream:
            async for chunk in stream:
                await websocket.send_json(chunk.model_dump(mode="json", exclude_none=True))
        await websocket.close(code=1000)
    except WebSocketDisconnect:
        # Client went away mid-stream; exit quietly (the generator was closed by aclosing).
        return
