"""FastAPI routes for the personas feature (Slice 15, §5.3 / §5.4).

Endpoints (all require an authenticated user via ``get_current_user``):
  GET    /api/v1/personas              List the caller's personas: the four built-ins +
                                       the caller's own custom personas.
  POST   /api/v1/personas              Create a custom persona owned by the caller.
  PATCH  /api/v1/personas/{id}         Edit one of the caller's own custom personas.
  DELETE /api/v1/personas/{id}         Delete one of the caller's own custom personas.

Domain exceptions translate via the registered handlers (app.core.errors.handlers):
  NotFoundError              → 404  (built-in / foreign / missing — no existence disclosure)
  PersonaNameConflictError   → 409  (subclasses ConflictError; duplicate name for the caller)
  AuthenticationError        → 401  (raised by get_current_user)
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.features.auth.deps import get_current_user
from app.features.auth.models import User
from app.features.personas import service
from app.features.personas.schemas import Persona, PersonaCreate, PersonaList, PersonaUpdate

router = APIRouter(prefix="/api/v1/personas", tags=["personas"])

_NOT_FOUND = {"description": "Persona not found or not owned by caller (built-ins included)"}
_NAME_CONFLICT = {"description": "The caller already has a custom persona with this name"}
_UNAUTH = {"description": "Not authenticated"}


@router.get("", response_model=PersonaList, operation_id="list_personas", responses={401: _UNAUTH})
async def list_personas(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> PersonaList:
    """List the personas available to the caller: the four built-ins plus the caller's own."""
    return await service.list_personas(db, requester=current_user)


@router.post(
    "",
    response_model=Persona,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_persona",
    responses={401: _UNAUTH, 409: _NAME_CONFLICT},
)
async def create_persona(
    body: PersonaCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Persona:
    """Create a custom persona owned by the caller (§5.3 "create … their own")."""
    persona = await service.create_persona(
        db, requester=current_user, name=body.name, system_prompt=body.system_prompt
    )
    await db.commit()
    return persona


@router.patch(
    "/{persona_id}",
    response_model=Persona,
    operation_id="update_persona",
    responses={401: _UNAUTH, 404: _NOT_FOUND, 409: _NAME_CONFLICT},
)
async def update_persona(
    persona_id: UUID,
    body: PersonaUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Persona:
    """Edit one of the caller's own custom personas (§5.3). A built-in or another user's
    persona is invisible (404), so it cannot be edited."""
    persona = await service.update_persona(
        db,
        requester=current_user,
        persona_id=persona_id,
        name=body.name,
        system_prompt=body.system_prompt,
    )
    await db.commit()
    return persona


@router.delete(
    "/{persona_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_persona",
    responses={401: _UNAUTH, 404: _NOT_FOUND},
)
async def delete_persona(
    persona_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Delete one of the caller's own custom personas (§5.3). A built-in or another user's
    persona returns 404 (cannot be deleted)."""
    await service.delete_persona(db, requester=current_user, persona_id=persona_id)
    await db.commit()
