"""Health check endpoint — no auth required.

Deliberately layerless: this is a pure liveness probe with no domain logic, no
persistence, and no request schema, so it omits the canonical schemas/models/
service/repository layers (CLAUDE.md's per-feature layout is for features that
own data). If health ever grows state (dependency checks, readiness), add the
layers then rather than carrying empty stubs now.
"""

from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1", tags=["health"])


def _get_version() -> str:
    try:
        return version("adeptus-backend")
    except PackageNotFoundError:
        return "dev"


@router.get("/health", response_model=dict)
async def health_check() -> dict:
    return {"status": "ok", "version": _get_version()}
