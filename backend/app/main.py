"""FastAPI application entry point."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.db import get_sessionmaker
from app.core.errors import register_error_handlers
from app.features.auth import service as auth_service
from app.features.auth.router import router as auth_router
from app.features.engagements.router import router as engagements_router
from app.features.health.router import router as health_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Run startup tasks: bootstrap the admin user."""
    factory = get_sessionmaker()
    async with factory() as db:
        try:
            user = await auth_service.bootstrap_admin(db)
            if user:
                await db.commit()
                logger.info("Admin user bootstrapped: %s", user.username)
            else:
                logger.info("Admin user already exists — skipping bootstrap.")
        except Exception:
            logger.exception("Failed to bootstrap admin user")
            # Don't crash the app — bad hash is a config problem, not a fatal startup error.
            # The admin simply won't be able to log in until the env var is fixed.
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Adeptus", lifespan=lifespan)
    register_error_handlers(app)
    # feature routers
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(engagements_router)
    return app


app = create_app()
