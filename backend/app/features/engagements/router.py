"""FastAPI routes for engagement CRUD and membership; HTTP-level error translation only."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/engagements", tags=["engagements"])

# Endpoints are defined in slice-01 backend task 5.
