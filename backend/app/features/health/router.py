"""Health check endpoint — no auth required."""

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
