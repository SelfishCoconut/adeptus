"""Startup-bootstrap test (mirrors the admin bootstrap test).

Asserts the four built-ins exist after one seed pass and are unchanged after a second
(idempotent — no duplicates, content matches the seed constants).
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.personas import service
from app.features.personas.models import Persona
from app.features.personas.seed import SYSTEM_PERSONAS

pytestmark = pytest.mark.asyncio


async def _builtin_count(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count()).select_from(Persona).where(Persona.is_builtin.is_(True))
    )
    return int(result.scalar_one())


async def test_bootstrap_seeds_four_builtins(db_session: AsyncSession) -> None:
    await service.bootstrap_system_personas(db_session)
    await db_session.commit()

    assert await _builtin_count(db_session) == 4
    rows = (
        (await db_session.execute(select(Persona).where(Persona.is_builtin.is_(True))))
        .scalars()
        .all()
    )
    by_slug = {r.slug: r for r in rows}
    for seed in SYSTEM_PERSONAS:
        assert by_slug[seed.slug].name == seed.name
        assert by_slug[seed.slug].system_prompt == seed.system_prompt
        assert by_slug[seed.slug].user_id is None


async def test_bootstrap_idempotent_second_pass(db_session: AsyncSession) -> None:
    await service.bootstrap_system_personas(db_session)
    await db_session.commit()
    # A second pass inserts nothing and leaves exactly four built-ins.
    await service.bootstrap_system_personas(db_session)
    await db_session.commit()

    assert await _builtin_count(db_session) == 4
    general = (
        await db_session.execute(select(Persona).where(Persona.slug == "general"))
    ).scalar_one()
    assert general.system_prompt == next(
        p.system_prompt for p in SYSTEM_PERSONAS if p.slug == "general"
    )
