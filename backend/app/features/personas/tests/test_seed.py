"""Seed-data tests — exactly four built-ins, the expected slugs, and the no-drift guard.

The ``general`` built-in's prompt is asserted byte-equal to chat's base ``SYSTEM_PROMPT``
(Risk 3): if the default behavior ever silently diverges, this fails.
"""

from app.features.chat import service as chat_service
from app.features.personas.seed import GENERAL_SLUG, SYSTEM_PERSONAS


def test_exactly_four_builtins() -> None:
    assert len(SYSTEM_PERSONAS) == 4


def test_expected_slugs() -> None:
    slugs = {p.slug for p in SYSTEM_PERSONAS}
    assert slugs == {"general", "recon", "web-exploit", "report-writer"}


def test_slugs_are_unique() -> None:
    slugs = [p.slug for p in SYSTEM_PERSONAS]
    assert len(slugs) == len(set(slugs))


def test_every_builtin_has_name_and_prompt() -> None:
    for p in SYSTEM_PERSONAS:
        assert p.name
        assert p.system_prompt


def test_general_prompt_matches_chat_default() -> None:
    """general's seeded prompt is the single source of truth for chat's base prompt."""
    general = next(p for p in SYSTEM_PERSONAS if p.slug == GENERAL_SLUG)
    assert general.system_prompt == chat_service.SYSTEM_PROMPT
