"""FastAPI application entry point."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.errors import register_error_handlers
from app.features.audit.router import router as audit_router
from app.features.auth import service as auth_service
from app.features.auth.router import router as auth_router
from app.features.chat.router import router as chat_router
from app.features.engagements import events as engagement_events
from app.features.engagements.router import router as engagements_router
from app.features.graph import writer as graph_writer
from app.features.graph.router import router as graph_router
from app.features.health.router import router as health_router
from app.features.mcp import concurrency as mcp_concurrency
from app.features.mcp import listeners as mcp_listeners
from app.features.mcp import repository as mcp_repo
from app.features.mcp import subprocess_manager
from app.features.mcp.registry import ConfigError, load_registry
from app.features.mcp.router import router as mcp_router
from app.features.personas import service as personas_service
from app.features.personas.router import router as personas_router

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

        # Seed the four global built-in personas idempotently (Slice 15, Decision 5),
        # mirroring the admin bootstrap: safe on every boot (upsert by slug), non-fatal on
        # error (a bad seed must not crash the app; personas simply won't be present until
        # the next successful boot).
        try:
            seeded = await personas_service.bootstrap_system_personas(db)
            await db.commit()
            logger.info("Seeded %d built-in persona(s)", seeded)
        except Exception:
            logger.exception("Failed to bootstrap system personas")

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

    # Startup reconciliation (Slice 05 Task 8 / Slice 03 Risk 3).
    # After a restart the in-process queue is empty, so any row still in
    # 'queued' or 'running' is a phantom — mark it 'failed' immediately.
    # Full crash-recovery (stdout/stderr preservation, etc.) is Slice 38.
    try:
        factory2 = get_sessionmaker()
        async with factory2() as db:
            stale = await mcp_repo.reconcile_stale_tool_runs(db)
            await db.commit()
            if stale:
                logger.warning("Reconciled %d stale tool_run(s) to 'failed' on startup", stale)
            else:
                logger.debug("Startup reconciliation: no stale tool_runs found")
    except Exception:
        logger.exception("Failed to reconcile stale tool_runs on startup — continuing")

    yield

    # Shut down MCP subprocesses on app shutdown.
    await subprocess_manager.shutdown()
    logger.info("MCP subprocess manager stopped")

    # Cancel all per-engagement single-writer consumer tasks and clear the
    # registry (graph feature, Slice 07 / ADR-0001).  In-process state only —
    # nothing to persist; warm-start rebuilds from Postgres on next access.
    graph_writer.shutdown()
    logger.info("Graph single-writer registry shut down")


def create_app() -> FastAPI:
    app = FastAPI(title="Adeptus", lifespan=lifespan)
    register_error_handlers(app)
    # Composition-root wiring: an engagement slot-limit change notifies the
    # in-process concurrency manager.  Registered here (not inside either feature)
    # so the engagements feature stays ignorant of mcp; the dependency flows
    # mcp → engagements via the events seam.  Idempotent across create_app() calls.
    engagement_events.on_slot_limit_changed(mcp_concurrency.set_slot_limit)
    # Slice 06 (task 7): an engagement pause/resume change notifies the in-process
    # concurrency manager so it can kill/de-queue in-flight runs.  The listener
    # (defined in mcp/listeners.py) returns (killed_running, dequeued) counts via
    # the event-dispatch return value — keeping the dependency direction mcp →
    # engagements (engagements never imports mcp).  Idempotent.
    engagement_events.on_engagement_paused_changed(mcp_listeners.on_engagement_paused_changed)
    # feature routers
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(engagements_router)
    app.include_router(mcp_router)
    app.include_router(graph_router)
    app.include_router(audit_router)
    app.include_router(chat_router)
    app.include_router(personas_router)
    return app


app = create_app()
