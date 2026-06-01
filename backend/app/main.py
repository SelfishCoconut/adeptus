"""FastAPI application entry point."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.errors import register_error_handlers
from app.features.auth import service as auth_service
from app.features.auth.router import router as auth_router
from app.features.engagements.router import router as engagements_router
from app.features.health.router import router as health_router
from app.features.mcp import subprocess_manager
from app.features.mcp.registry import ConfigError, load_registry
from app.features.mcp.router import router as mcp_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Run startup tasks: bootstrap the admin user, start MCP subprocesses."""
    settings = get_settings()
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

        # DEV/TEST ONLY — never runs when ENVIRONMENT=production
        if settings.ENVIRONMENT in ("development", "test"):
            try:
                test_user = await auth_service.bootstrap_test_user(db)
                if test_user:
                    await db.commit()
                    logger.info("Test user bootstrapped: %s", test_user.username)
                else:
                    logger.debug("Test user bootstrap skipped (already exists or vars unset).")
            except Exception:
                logger.exception("Failed to bootstrap test user")
                # Don't crash startup — misconfigured test-user vars are not fatal.

    # Load the MCP registry and spawn configured subprocesses.
    # A missing or malformed config is non-fatal: the servers will simply show
    # as "stopped" until the config is provided and the app restarted.
    # Integration/dev environments provide the config via task 11 (mcp.yaml +
    # MCP_CONFIG_PATH env var).  Unit tests run without a real config file.
    try:
        load_registry()
        await subprocess_manager.startup()
        logger.info("MCP subprocess manager started")
    except ConfigError as exc:
        logger.warning("MCP config unavailable — MCP servers will not be started: %s", exc)
    except Exception:
        logger.exception(
            "Unexpected error starting MCP subprocess manager — continuing without MCP"
        )

    # TODO (Slice 38): on startup, sweep tool_runs rows with finished_at IS NULL
    # and mark them exit_code=-1, stderr='Backend restarted before completion'
    # (Risk 3 in slice-03 spec).  Deferred because it requires a DB session at
    # startup and full crash-recovery semantics are formalised in Slice 38.

    yield

    # Shut down MCP subprocesses on app shutdown.
    await subprocess_manager.shutdown()
    logger.info("MCP subprocess manager stopped")


def create_app() -> FastAPI:
    app = FastAPI(title="Adeptus", lifespan=lifespan)
    register_error_handlers(app)
    # feature routers
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(engagements_router)
    app.include_router(mcp_router)
    return app


app = create_app()
