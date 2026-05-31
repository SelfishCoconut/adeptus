"""Repository-layer tests for the engagements feature.

All tests use an in-memory SQLite async engine (see conftest.py for fixture).
Tests are async; pytest-asyncio is configured with asyncio_mode="auto" in
pyproject.toml so no explicit @pytest.mark.asyncio decorator is required.
"""

from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.auth.models import User
from app.features.auth.repository import create_user
from app.features.engagements import repository as repo
from app.features.engagements.models import Engagement  # noqa: TCH001

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uid(obj: Engagement | User) -> UUID:
    """Cast a SQLAlchemy UUID column value to plain uuid.UUID for type-safe calls."""
    return cast(UUID, obj.id)


async def _make_user(db: AsyncSession, *, username: str = "alice") -> User:
    """Create and flush a User via the auth repository."""
    return await create_user(
        db,
        username=username,
        password_hash="$argon2id$v=19$m=65536,t=3,p=4$...",
    )


async def _make_engagement(
    db: AsyncSession,
    owner_id: UUID,
    *,
    name: str = "Test Engagement",
) -> Engagement:
    """Create a minimal engagement owned by owner_id."""
    return await repo.create_engagement(
        db,
        name=name,
        scope="10.0.0.0/8",
        client_info=None,
        owner_id=owner_id,
    )


# ---------------------------------------------------------------------------
# create_engagement
# ---------------------------------------------------------------------------


async def test_create_engagement_returns_engagement_with_id(db_session: AsyncSession) -> None:
    owner = await _make_user(db_session)
    engagement = await _make_engagement(db_session, _uid(owner))

    assert engagement.id is not None
    assert engagement.name == "Test Engagement"
    assert engagement.scope == "10.0.0.0/8"
    assert engagement.client_info is None


async def test_create_engagement_auto_creates_owner_member(db_session: AsyncSession) -> None:
    owner = await _make_user(db_session)
    engagement = await _make_engagement(db_session, _uid(owner))

    member = await repo.get_member(db_session, _uid(engagement), _uid(owner))

    assert member is not None
    assert member.role == "owner"


# ---------------------------------------------------------------------------
# get_engagement_for_member  (§17.1 chokepoint)
# ---------------------------------------------------------------------------


async def test_get_engagement_for_member_returns_engagement_for_member(
    db_session: AsyncSession,
) -> None:
    owner = await _make_user(db_session)
    engagement = await _make_engagement(db_session, _uid(owner))

    result = await repo.get_engagement_for_member(db_session, _uid(engagement), _uid(owner))

    assert result is not None
    eng, member = result
    assert eng.id == engagement.id
    assert member.role == "owner"


async def test_get_engagement_for_member_returns_none_for_non_member(
    db_session: AsyncSession,
) -> None:
    owner = await _make_user(db_session, username="owner")
    outsider = await _make_user(db_session, username="outsider")
    engagement = await _make_engagement(db_session, _uid(owner))

    result = await repo.get_engagement_for_member(db_session, _uid(engagement), _uid(outsider))

    assert result is None


async def test_get_engagement_for_member_returns_none_for_unknown_engagement(
    db_session: AsyncSession,
) -> None:
    owner = await _make_user(db_session)

    result = await repo.get_engagement_for_member(db_session, uuid4(), _uid(owner))

    assert result is None


# ---------------------------------------------------------------------------
# list_engagements_for_user
# ---------------------------------------------------------------------------


async def test_list_engagements_for_user_returns_own_engagements_with_role(
    db_session: AsyncSession,
) -> None:
    owner = await _make_user(db_session, username="owner")
    other = await _make_user(db_session, username="other")

    eng_a = await _make_engagement(db_session, _uid(owner), name="Engagement A")
    eng_b = await _make_engagement(db_session, _uid(owner), name="Engagement B")
    # other owns a third engagement that should NOT appear for owner
    await _make_engagement(db_session, _uid(other), name="Other's Engagement")

    results = await repo.list_engagements_for_user(db_session, _uid(owner))

    assert len(results) == 2
    ids = {r[0].id for r in results}
    assert _uid(eng_a) in ids
    assert _uid(eng_b) in ids
    # all returned as owner role
    roles = {r[1] for r in results}
    assert roles == {"owner"}


async def test_list_engagements_for_user_returns_correct_role_after_add_member(
    db_session: AsyncSession,
) -> None:
    owner = await _make_user(db_session, username="owner")
    invited = await _make_user(db_session, username="invited")
    engagement = await _make_engagement(db_session, _uid(owner))

    await repo.add_member(db_session, _uid(engagement), _uid(invited))

    results = await repo.list_engagements_for_user(db_session, _uid(invited))

    assert len(results) == 1
    assert results[0][1] == "member"


async def test_list_engagements_for_user_returns_empty_for_no_memberships(
    db_session: AsyncSession,
) -> None:
    owner = await _make_user(db_session, username="owner")
    user_no_engagements = await _make_user(db_session, username="nobody")
    await _make_engagement(db_session, _uid(owner))

    results = await repo.list_engagements_for_user(db_session, _uid(user_no_engagements))

    assert results == []


# ---------------------------------------------------------------------------
# get_members
# ---------------------------------------------------------------------------


async def test_get_members_returns_members_with_usernames(db_session: AsyncSession) -> None:
    owner = await _make_user(db_session, username="owner")
    member_user = await _make_user(db_session, username="bob")
    engagement = await _make_engagement(db_session, _uid(owner))
    await repo.add_member(db_session, _uid(engagement), _uid(member_user))

    members = await repo.get_members(db_session, _uid(engagement))

    assert len(members) == 2
    usernames = {m[1] for m in members}
    assert "owner" in usernames
    assert "bob" in usernames


async def test_get_members_returns_correct_roles(db_session: AsyncSession) -> None:
    owner = await _make_user(db_session, username="owner")
    member_user = await _make_user(db_session, username="member")
    engagement = await _make_engagement(db_session, _uid(owner))
    await repo.add_member(db_session, _uid(engagement), _uid(member_user))

    members = await repo.get_members(db_session, _uid(engagement))

    role_by_username = {username: em.role for em, username in members}
    assert role_by_username["owner"] == "owner"
    assert role_by_username["member"] == "member"


async def test_get_members_returns_one_for_fresh_engagement(
    db_session: AsyncSession,
) -> None:
    """A freshly created engagement has exactly one member (the owner)."""
    owner = await _make_user(db_session)
    engagement = await _make_engagement(db_session, _uid(owner))

    members = await repo.get_members(db_session, _uid(engagement))

    assert len(members) == 1
    assert members[0][1] == "alice"  # username of the owner


# ---------------------------------------------------------------------------
# add_member / get_member
# ---------------------------------------------------------------------------


async def test_add_member_adds_with_role_member(db_session: AsyncSession) -> None:
    owner = await _make_user(db_session, username="owner")
    invitee = await _make_user(db_session, username="invitee")
    engagement = await _make_engagement(db_session, _uid(owner))

    added = await repo.add_member(db_session, _uid(engagement), _uid(invitee))

    assert added.role == "member"
    assert cast(UUID, added.engagement_id) == _uid(engagement)
    assert cast(UUID, added.user_id) == _uid(invitee)


async def test_get_member_returns_row_after_add(db_session: AsyncSession) -> None:
    owner = await _make_user(db_session, username="owner")
    invitee = await _make_user(db_session, username="invitee")
    engagement = await _make_engagement(db_session, _uid(owner))
    await repo.add_member(db_session, _uid(engagement), _uid(invitee))

    found = await repo.get_member(db_session, _uid(engagement), _uid(invitee))

    assert found is not None
    assert found.role == "member"


async def test_get_member_returns_none_when_not_a_member(db_session: AsyncSession) -> None:
    owner = await _make_user(db_session, username="owner")
    outsider = await _make_user(db_session, username="outsider")
    engagement = await _make_engagement(db_session, _uid(owner))

    found = await repo.get_member(db_session, _uid(engagement), _uid(outsider))

    assert found is None


# ---------------------------------------------------------------------------
# remove_member
# ---------------------------------------------------------------------------


async def test_remove_member_deletes_the_row(db_session: AsyncSession) -> None:
    owner = await _make_user(db_session, username="owner")
    invitee = await _make_user(db_session, username="invitee")
    engagement = await _make_engagement(db_session, _uid(owner))
    await repo.add_member(db_session, _uid(engagement), _uid(invitee))

    await repo.remove_member(db_session, _uid(engagement), _uid(invitee))

    found = await repo.get_member(db_session, _uid(engagement), _uid(invitee))
    assert found is None


async def test_remove_member_is_idempotent_for_missing_row(db_session: AsyncSession) -> None:
    """Removing a user who is not a member should not raise."""
    owner = await _make_user(db_session, username="owner")
    outsider = await _make_user(db_session, username="outsider")
    engagement = await _make_engagement(db_session, _uid(owner))

    # Should not raise even though outsider is not a member.
    await repo.remove_member(db_session, _uid(engagement), _uid(outsider))


async def test_remove_member_does_not_remove_other_members(db_session: AsyncSession) -> None:
    owner = await _make_user(db_session, username="owner")
    member_a = await _make_user(db_session, username="member_a")
    member_b = await _make_user(db_session, username="member_b")
    engagement = await _make_engagement(db_session, _uid(owner))
    await repo.add_member(db_session, _uid(engagement), _uid(member_a))
    await repo.add_member(db_session, _uid(engagement), _uid(member_b))

    await repo.remove_member(db_session, _uid(engagement), _uid(member_a))

    assert await repo.get_member(db_session, _uid(engagement), _uid(member_a)) is None
    assert await repo.get_member(db_session, _uid(engagement), _uid(member_b)) is not None


# ---------------------------------------------------------------------------
# Isolation guard: get_engagement_for_member after membership change
# ---------------------------------------------------------------------------


async def test_engagement_invisible_after_member_removed(db_session: AsyncSession) -> None:
    """After removal, get_engagement_for_member returns None for the removed user."""
    owner = await _make_user(db_session, username="owner")
    invited = await _make_user(db_session, username="invited")
    engagement = await _make_engagement(db_session, _uid(owner))
    await repo.add_member(db_session, _uid(engagement), _uid(invited))

    # Visible while a member.
    assert (
        await repo.get_engagement_for_member(db_session, _uid(engagement), _uid(invited))
        is not None
    )

    await repo.remove_member(db_session, _uid(engagement), _uid(invited))

    # No longer visible after removal — §17.1 isolation.
    assert await repo.get_engagement_for_member(db_session, _uid(engagement), _uid(invited)) is None


async def test_engagement_visible_after_add_member(db_session: AsyncSession) -> None:
    """get_engagement_for_member returns the engagement once a user has been added."""
    owner = await _make_user(db_session, username="owner")
    new_member = await _make_user(db_session, username="new_member")
    engagement = await _make_engagement(db_session, _uid(owner))

    # Not visible before being added.
    assert (
        await repo.get_engagement_for_member(db_session, _uid(engagement), _uid(new_member)) is None
    )

    await repo.add_member(db_session, _uid(engagement), _uid(new_member))

    # Now visible.
    assert (
        await repo.get_engagement_for_member(db_session, _uid(engagement), _uid(new_member))
        is not None
    )


# ---------------------------------------------------------------------------
# Additional coverage
# ---------------------------------------------------------------------------


async def test_create_engagement_with_client_info(db_session: AsyncSession) -> None:
    owner = await _make_user(db_session)
    engagement = await repo.create_engagement(
        db_session,
        name="Detailed Engagement",
        scope="192.168.1.0/24",
        client_info="Acme Corp",
        owner_id=_uid(owner),
    )

    assert engagement.client_info == "Acme Corp"


async def test_create_multiple_engagements_independent_memberships(
    db_session: AsyncSession,
) -> None:
    user_a = await _make_user(db_session, username="user_a")
    user_b = await _make_user(db_session, username="user_b")

    eng_a = await _make_engagement(db_session, _uid(user_a), name="Eng A")
    eng_b = await _make_engagement(db_session, _uid(user_b), name="Eng B")

    # user_a can only see eng_a
    assert await repo.get_engagement_for_member(db_session, _uid(eng_a), _uid(user_a)) is not None
    assert await repo.get_engagement_for_member(db_session, _uid(eng_b), _uid(user_a)) is None

    # user_b can only see eng_b
    assert await repo.get_engagement_for_member(db_session, _uid(eng_b), _uid(user_b)) is not None
    assert await repo.get_engagement_for_member(db_session, _uid(eng_a), _uid(user_b)) is None


async def test_list_engagements_for_user_only_shows_own(db_session: AsyncSession) -> None:
    """A user not in any engagement sees an empty list, even when engagements exist."""
    owner = await _make_user(db_session, username="owner")
    stranger = await _make_user(db_session, username="stranger")

    await _make_engagement(db_session, _uid(owner), name="Private A")
    await _make_engagement(db_session, _uid(owner), name="Private B")

    result = await repo.list_engagements_for_user(db_session, _uid(stranger))

    assert result == []


async def test_remove_owner_membership_at_repository_level(db_session: AsyncSession) -> None:
    """The repository remove_member does NOT block owner self-removal.

    That enforcement lives in the service layer.  This test verifies the raw
    repository call works so the service has something to call after its guard.
    """
    owner = await _make_user(db_session)
    engagement = await _make_engagement(db_session, _uid(owner))

    # Repository-level: no guard — succeeds (service layer enforces the invariant).
    await repo.remove_member(db_session, _uid(engagement), _uid(owner))

    assert await repo.get_member(db_session, _uid(engagement), _uid(owner)) is None


@pytest.mark.parametrize("engagements_count", [0, 1, 3])
async def test_list_engagements_count(db_session: AsyncSession, engagements_count: int) -> None:
    owner = await _make_user(db_session)
    for i in range(engagements_count):
        await _make_engagement(db_session, _uid(owner), name=f"Eng {i}")

    results = await repo.list_engagements_for_user(db_session, _uid(owner))

    assert len(results) == engagements_count


# ---------------------------------------------------------------------------
# privacy_mode — create and update
# ---------------------------------------------------------------------------


async def test_repo_create_with_privacy_mode(db_session: AsyncSession) -> None:
    """create_engagement persists the supplied privacy_mode value."""
    owner = await _make_user(db_session)
    engagement = await repo.create_engagement(
        db_session,
        name="Cloud Engagement",
        scope="10.0.0.0/8",
        client_info=None,
        owner_id=_uid(owner),
        privacy_mode="cloud_enabled",
    )

    assert engagement.privacy_mode == "cloud_enabled"


async def test_repo_update_privacy_mode(db_session: AsyncSession) -> None:
    """update_engagement changes the privacy_mode column and returns the refreshed row."""
    owner = await _make_user(db_session)
    engagement = await _make_engagement(db_session, _uid(owner))

    # Default is local_only.
    assert engagement.privacy_mode == "local_only"

    updated = await repo.update_engagement(
        db_session, _uid(engagement), privacy_mode="cloud_enabled"
    )

    assert updated is not None
    assert updated.privacy_mode == "cloud_enabled"
    assert updated.id == engagement.id
