"""Alembic env.py — async SQLAlchemy + FastAPI settings integration."""

import asyncio
from logging.config import fileConfig

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.core.config import get_settings
from app.core.db import Base

# Import all models so their metadata is registered on Base.
from app.features.approvals import models as _approvals_models  # noqa: F401
from app.features.audit import models as _audit_models  # noqa: F401
from app.features.auth import models as _auth_models  # noqa: F401
from app.features.chat import models as _chat_models  # noqa: F401
from app.features.engagements import models as _engagements_models  # noqa: F401
from app.features.graph import models as _graph_models  # noqa: F401
from app.features.mcp import models as _mcp_models  # noqa: F401
from app.features.personas import models as _personas_models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    return get_settings().DATABASE_URL


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generates SQL without a live connection)."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode using an async engine."""
    engine = create_async_engine(get_url())
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
