"""FastAPI routes for authentication: login, logout, me, accept-terms."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
