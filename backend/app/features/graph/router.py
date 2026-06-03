"""FastAPI routes for the graph feature (task 6). HTTP-level error translation
only; all writes route through the service → single writer."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1", tags=["graph"])
