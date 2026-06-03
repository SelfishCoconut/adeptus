"""Tests for the engagements service layer.

The repository and auth repository are fully mocked with AsyncMock so these
tests have no database dependency.  Every error path from the spec's Test plan
section is covered.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.core.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.features.engagements import service
from app.features.engagements.schemas import AddMemberRequest, EngagementCreate, EngagementUpdate

# ---------------------------------------------------------------------------
# Helpers — build lightweight mock objects that carry the attributes the
# service reads from Engagement, EngagementMember, and User.
# ---------------------------------------------------------------------------

NOW = datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)


def _make_engagement(
    *,
    engagement_id: UUID | None = None,
    name: str = "Test Engagement",
    scope: str = "*.example.com",
    client_info: str | None = "ACME Corp",
    status: str = "active",
    privacy_mode: str = "local_only",
    concurrency_slot_limit: int = 3,
    paused: bool = False,
) -> MagicMock:
    eng = MagicMock()
    eng.id = engagement_id or uuid4()
    eng.name = name
    eng.scope = scope
    eng.client_info = client_info
    eng.status = status
    eng.privacy_mode = privacy_mode
    eng.concurrency_slot_limit = concurrency_slot_limit
    eng.paused = paused
    eng.created_at = NOW
    eng.updated_at = NOW
    return eng


def _make_member(
    *,
    engagement_id: UUID | None = None,
    user_id: UUID | None = None,
    role: str = "member",
) -> MagicMock:
    m = MagicMock()
    m.engagement_id = engagement_id or uuid4()
    m.user_id = user_id or uuid4()
    m.role = role
    m.joined_at = NOW
    return m


def _make_user(
    *,
    user_id: UUID | None = None,
    username: str = "alice",
    role: str = "user",
) -> MagicMock:
    u = MagicMock()
    u.id = user_id or uuid4()
    u.username = username
    u.role = role
    return u


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db() -> AsyncMock:
    return AsyncMock()


@pytest.fixture()
def caller() -> MagicMock:
    return _make_user(username="alice")


# ---------------------------------------------------------------------------
# create_engagement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_engagement_returns_detail(db: AsyncMock, caller: MagicMock) -> None:
    """create_engagement returns an EngagementDetail with all fields populated."""
    eng_id = uuid4()
    mock_eng = _make_engagement(
        engagement_id=eng_id,
        name="Alpha",
        scope="*.alpha.com",
        client_info="Corp A",
    )
    data = EngagementCreate(name="Alpha", scope="*.alpha.com", client_info="Corp A")

    with patch(
        "app.features.engagements.service.repo.create_engagement",
        new=AsyncMock(return_value=mock_eng),
    ):
        result = await service.create_engagement(db, caller, data)

    assert result.id == eng_id
    assert result.name == "Alpha"
    assert result.scope == "*.alpha.com"
    assert result.client_info == "Corp A"
    assert result.status == "active"
    assert result.member_role == "owner"
    assert result.created_at == NOW
    assert result.updated_at == NOW


@pytest.mark.asyncio
async def test_create_engagement_auto_adds_owner_member(db: AsyncMock, caller: MagicMock) -> None:
    """create_engagement passes caller.id as owner_id to the repository."""
    mock_eng = _make_engagement()
    data = EngagementCreate(name="Beta", scope="10.0.0.0/8")

    with patch(
        "app.features.engagements.service.repo.create_engagement",
        new=AsyncMock(return_value=mock_eng),
    ) as mock_create:
        await service.create_engagement(db, caller, data)

    mock_create.assert_awaited_once_with(
        db,
        name="Beta",
        scope="10.0.0.0/8",
        client_info=None,
        owner_id=caller.id,
        privacy_mode="local_only",
    )


@pytest.mark.asyncio
async def test_create_engagement_any_authenticated_user_may_create(db: AsyncMock) -> None:
    """create_engagement succeeds for a non-admin user — no role restriction."""
    regular_user = _make_user(username="bob", role="user")
    mock_eng = _make_engagement()
    data = EngagementCreate(name="Gamma", scope="192.168.0.0/24")

    with patch(
        "app.features.engagements.service.repo.create_engagement",
        new=AsyncMock(return_value=mock_eng),
    ):
        # Must not raise ForbiddenError or any other exception.
        result = await service.create_engagement(db, regular_user, data)

    assert result.member_role == "owner"


# ---------------------------------------------------------------------------
# list_engagements
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_engagements_only_returns_own(db: AsyncMock, caller: MagicMock) -> None:
    """list_engagements returns only the caller's engagements with correct roles."""
    eng_a = _make_engagement(name="Alpha")
    eng_b = _make_engagement(name="Beta")
    rows = [(eng_a, "owner"), (eng_b, "member")]

    with patch(
        "app.features.engagements.service.repo.list_engagements_for_user",
        new=AsyncMock(return_value=rows),
    ):
        result = await service.list_engagements(db, caller)

    assert len(result) == 2
    assert result[0].name == "Alpha"
    assert result[0].member_role == "owner"
    assert result[1].name == "Beta"
    assert result[1].member_role == "member"


# ---------------------------------------------------------------------------
# get_engagement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_engagement_member_can_read(db: AsyncMock, caller: MagicMock) -> None:
    """get_engagement returns EngagementDetail when the caller is a member.

    get_engagement_for_member now returns (Engagement, EngagementMember) in a
    single query — no second get_member call.
    """
    eng_id = uuid4()
    mock_eng = _make_engagement(engagement_id=eng_id)
    mock_membership = _make_member(engagement_id=eng_id, user_id=caller.id, role="owner")

    with patch(
        "app.features.engagements.service.repo.get_engagement_for_member",
        new=AsyncMock(return_value=(mock_eng, mock_membership)),
    ):
        result = await service.get_engagement(db, caller, eng_id)

    assert result.id == eng_id
    assert result.member_role == "owner"


@pytest.mark.asyncio
async def test_get_engagement_non_member_returns_not_found(
    db: AsyncMock, caller: MagicMock
) -> None:
    """get_engagement raises NotFoundError when the caller is not a member (§17.1)."""
    eng_id = uuid4()

    with patch(
        "app.features.engagements.service.repo.get_engagement_for_member",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(NotFoundError):
            await service.get_engagement(db, caller, eng_id)


# ---------------------------------------------------------------------------
# list_members
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_members_non_member_returns_404(db: AsyncMock, caller: MagicMock) -> None:
    """list_members raises NotFoundError when the caller is not a member (§17.1)."""
    eng_id = uuid4()

    with patch(
        "app.features.engagements.service.repo.get_member",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(NotFoundError):
            await service.list_members(db, caller, eng_id)


@pytest.mark.asyncio
async def test_list_members_member_can_list(db: AsyncMock, caller: MagicMock) -> None:
    """list_members returns MemberEntry list for a valid member caller."""
    eng_id = uuid4()
    other_id = uuid4()
    mock_membership = _make_member(engagement_id=eng_id, user_id=caller.id, role="owner")
    other_member = _make_member(engagement_id=eng_id, user_id=other_id, role="member")
    rows = [(mock_membership, "alice"), (other_member, "bob")]

    with (
        patch(
            "app.features.engagements.service.repo.get_member",
            new=AsyncMock(return_value=mock_membership),
        ),
        patch(
            "app.features.engagements.service.repo.get_members",
            new=AsyncMock(return_value=rows),
        ),
    ):
        result = await service.list_members(db, caller, eng_id)

    assert len(result) == 2
    assert result[0].username == "alice"
    assert result[1].username == "bob"


# ---------------------------------------------------------------------------
# add_member
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_member_owner_succeeds(db: AsyncMock, caller: MagicMock) -> None:
    """add_member succeeds when the caller is the owner and the target is new."""
    eng_id = uuid4()
    target = _make_user(username="charlie")
    caller_member = _make_member(engagement_id=eng_id, user_id=caller.id, role="owner")
    new_member_row = _make_member(engagement_id=eng_id, user_id=target.id, role="member")
    request = AddMemberRequest(username="charlie")

    # get_member: first call is for caller, second is for target existence check.
    get_member_mock = AsyncMock(side_effect=[caller_member, None])

    with (
        patch(
            "app.features.engagements.service.repo.get_member",
            new=get_member_mock,
        ),
        patch(
            "app.features.engagements.service.auth_repo.get_user_by_username",
            new=AsyncMock(return_value=target),
        ),
        patch(
            "app.features.engagements.service.repo.add_member",
            new=AsyncMock(return_value=new_member_row),
        ),
    ):
        result = await service.add_member(db, caller, eng_id, request)

    assert result.username == "charlie"
    assert result.role == "member"
    assert result.user_id == target.id


@pytest.mark.asyncio
async def test_add_member_non_member_caller_returns_404(db: AsyncMock, caller: MagicMock) -> None:
    """add_member raises NotFoundError (not ForbiddenError) when caller is not a member (§17.1)."""
    eng_id = uuid4()
    request = AddMemberRequest(username="charlie")

    with patch(
        "app.features.engagements.service.repo.get_member",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(NotFoundError):
            await service.add_member(db, caller, eng_id, request)


@pytest.mark.asyncio
async def test_add_member_non_owner_returns_403(db: AsyncMock, caller: MagicMock) -> None:
    """add_member raises ForbiddenError when the caller is a member but not the owner."""
    eng_id = uuid4()
    caller_member = _make_member(engagement_id=eng_id, user_id=caller.id, role="member")
    request = AddMemberRequest(username="charlie")

    with patch(
        "app.features.engagements.service.repo.get_member",
        new=AsyncMock(return_value=caller_member),
    ):
        with pytest.raises(ForbiddenError):
            await service.add_member(db, caller, eng_id, request)


@pytest.mark.asyncio
async def test_add_member_unknown_username_returns_404(db: AsyncMock, caller: MagicMock) -> None:
    """add_member raises NotFoundError when the target username does not exist."""
    eng_id = uuid4()
    caller_member = _make_member(engagement_id=eng_id, user_id=caller.id, role="owner")
    request = AddMemberRequest(username="ghost")

    with (
        patch(
            "app.features.engagements.service.repo.get_member",
            new=AsyncMock(return_value=caller_member),
        ),
        patch(
            "app.features.engagements.service.auth_repo.get_user_by_username",
            new=AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(NotFoundError):
            await service.add_member(db, caller, eng_id, request)


@pytest.mark.asyncio
async def test_add_member_duplicate_returns_409(db: AsyncMock, caller: MagicMock) -> None:
    """add_member raises ConflictError when the target is already a member."""
    eng_id = uuid4()
    target = _make_user(username="dave")
    caller_member = _make_member(engagement_id=eng_id, user_id=caller.id, role="owner")
    existing_target_member = _make_member(engagement_id=eng_id, user_id=target.id, role="member")
    request = AddMemberRequest(username="dave")

    # get_member: first for caller (owner), second for target (already exists).
    get_member_mock = AsyncMock(side_effect=[caller_member, existing_target_member])

    with (
        patch(
            "app.features.engagements.service.repo.get_member",
            new=get_member_mock,
        ),
        patch(
            "app.features.engagements.service.auth_repo.get_user_by_username",
            new=AsyncMock(return_value=target),
        ),
    ):
        with pytest.raises(ConflictError):
            await service.add_member(db, caller, eng_id, request)


# ---------------------------------------------------------------------------
# remove_member
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_member_owner_succeeds(db: AsyncMock, caller: MagicMock) -> None:
    """remove_member succeeds when the owner removes a different member."""
    eng_id = uuid4()
    target_id = uuid4()
    caller_member = _make_member(engagement_id=eng_id, user_id=caller.id, role="owner")
    target_member = _make_member(engagement_id=eng_id, user_id=target_id, role="member")

    # get_member: first for caller, second for target.
    get_member_mock = AsyncMock(side_effect=[caller_member, target_member])
    remove_mock = AsyncMock(return_value=None)

    with (
        patch(
            "app.features.engagements.service.repo.get_member",
            new=get_member_mock,
        ),
        patch(
            "app.features.engagements.service.repo.remove_member",
            new=remove_mock,
        ),
    ):
        await service.remove_member(db, caller, eng_id, target_id)

    remove_mock.assert_awaited_once_with(db, eng_id, target_id)


@pytest.mark.asyncio
async def test_remove_member_owner_cannot_remove_self_returns_400(
    db: AsyncMock, caller: MagicMock
) -> None:
    """remove_member raises BadRequestError when the owner tries to remove themselves."""
    eng_id = uuid4()
    caller_member = _make_member(engagement_id=eng_id, user_id=caller.id, role="owner")

    with patch(
        "app.features.engagements.service.repo.get_member",
        new=AsyncMock(return_value=caller_member),
    ):
        with pytest.raises(BadRequestError):
            await service.remove_member(db, caller, eng_id, caller.id)


@pytest.mark.asyncio
async def test_remove_member_non_member_caller_returns_404(
    db: AsyncMock, caller: MagicMock
) -> None:
    """remove_member raises NotFoundError (not 403) when caller is not a member (§17.1)."""
    eng_id = uuid4()
    target_id = uuid4()

    with patch(
        "app.features.engagements.service.repo.get_member",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(NotFoundError):
            await service.remove_member(db, caller, eng_id, target_id)


@pytest.mark.asyncio
async def test_remove_member_unknown_target_returns_404(db: AsyncMock, caller: MagicMock) -> None:
    """remove_member raises NotFoundError when the target user is not a member."""
    eng_id = uuid4()
    target_id = uuid4()
    caller_member = _make_member(engagement_id=eng_id, user_id=caller.id, role="owner")

    # get_member: caller is owner, target doesn't exist.
    get_member_mock = AsyncMock(side_effect=[caller_member, None])

    with patch(
        "app.features.engagements.service.repo.get_member",
        new=get_member_mock,
    ):
        with pytest.raises(NotFoundError):
            await service.remove_member(db, caller, eng_id, target_id)


# ---------------------------------------------------------------------------
# create_engagement — privacy_mode threading
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_engagement_default_privacy_mode(db: AsyncMock, caller: MagicMock) -> None:
    """create_engagement passes 'local_only' to the repository when no mode is specified."""
    mock_eng = _make_engagement(privacy_mode="local_only")
    data = EngagementCreate(name="Delta", scope="10.1.0.0/16")

    with patch(
        "app.features.engagements.service.repo.create_engagement",
        new=AsyncMock(return_value=mock_eng),
    ) as mock_create:
        result = await service.create_engagement(db, caller, data)

    mock_create.assert_awaited_once_with(
        db,
        name="Delta",
        scope="10.1.0.0/16",
        client_info=None,
        owner_id=caller.id,
        privacy_mode="local_only",
    )
    assert result.privacy_mode == "local_only"


@pytest.mark.asyncio
async def test_create_engagement_cloud_enabled(db: AsyncMock, caller: MagicMock) -> None:
    """create_engagement passes 'cloud_enabled' to the repository when explicitly set."""
    mock_eng = _make_engagement(privacy_mode="cloud_enabled")
    data = EngagementCreate(name="Cloud Eng", scope="10.2.0.0/16", privacy_mode="cloud_enabled")

    with patch(
        "app.features.engagements.service.repo.create_engagement",
        new=AsyncMock(return_value=mock_eng),
    ) as mock_create:
        result = await service.create_engagement(db, caller, data)

    mock_create.assert_awaited_once_with(
        db,
        name="Cloud Eng",
        scope="10.2.0.0/16",
        client_info=None,
        owner_id=caller.id,
        privacy_mode="cloud_enabled",
    )
    assert result.privacy_mode == "cloud_enabled"


# ---------------------------------------------------------------------------
# update_engagement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_engagement_owner_changes_mode(db: AsyncMock, caller: MagicMock) -> None:
    """Owner can flip privacy_mode from local_only to cloud_enabled."""
    eng_id = uuid4()
    mock_eng = _make_engagement(engagement_id=eng_id, privacy_mode="local_only")
    updated_eng = _make_engagement(engagement_id=eng_id, privacy_mode="cloud_enabled")
    caller_member = _make_member(engagement_id=eng_id, user_id=caller.id, role="owner")
    data = EngagementUpdate(privacy_mode="cloud_enabled")

    with (
        patch(
            "app.features.engagements.service.repo.get_engagement_for_member",
            new=AsyncMock(return_value=(mock_eng, caller_member)),
        ),
        patch(
            "app.features.engagements.service.repo.update_engagement",
            new=AsyncMock(return_value=updated_eng),
        ) as mock_update,
    ):
        result = await service.update_engagement(db, caller, eng_id, data)

    mock_update.assert_awaited_once_with(
        db, eng_id, privacy_mode="cloud_enabled", concurrency_slot_limit=None
    )
    assert result.privacy_mode == "cloud_enabled"
    assert result.member_role == "owner"


@pytest.mark.asyncio
async def test_update_engagement_non_owner_forbidden(db: AsyncMock, caller: MagicMock) -> None:
    """Non-owner member raises ForbiddenError when calling update_engagement."""
    eng_id = uuid4()
    mock_eng = _make_engagement(engagement_id=eng_id)
    caller_member = _make_member(engagement_id=eng_id, user_id=caller.id, role="member")
    data = EngagementUpdate(privacy_mode="cloud_enabled")

    with patch(
        "app.features.engagements.service.repo.get_engagement_for_member",
        new=AsyncMock(return_value=(mock_eng, caller_member)),
    ):
        with pytest.raises(ForbiddenError):
            await service.update_engagement(db, caller, eng_id, data)


@pytest.mark.asyncio
async def test_update_engagement_non_member_not_found(db: AsyncMock, caller: MagicMock) -> None:
    """Non-member raises NotFoundError per §17.1 isolation posture."""
    eng_id = uuid4()
    data = EngagementUpdate(privacy_mode="cloud_enabled")

    with patch(
        "app.features.engagements.service.repo.get_engagement_for_member",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(NotFoundError):
            await service.update_engagement(db, caller, eng_id, data)


# ---------------------------------------------------------------------------
# get_engagement — privacy_mode threading
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_engagement_returns_privacy_mode(db: AsyncMock, caller: MagicMock) -> None:
    """get_engagement returns EngagementDetail with privacy_mode present and correct."""
    eng_id = uuid4()
    mock_eng = _make_engagement(engagement_id=eng_id, privacy_mode="cloud_enabled")
    mock_membership = _make_member(engagement_id=eng_id, user_id=caller.id, role="member")

    with patch(
        "app.features.engagements.service.repo.get_engagement_for_member",
        new=AsyncMock(return_value=(mock_eng, mock_membership)),
    ):
        result = await service.get_engagement(db, caller, eng_id)

    assert result.privacy_mode == "cloud_enabled"
    assert result.id == eng_id


@pytest.mark.asyncio
async def test_list_engagements_returns_privacy_mode(db: AsyncMock, caller: MagicMock) -> None:
    """list_engagements returns EngagementSummary rows each carrying privacy_mode."""
    eng_a = _make_engagement(name="Local Eng", privacy_mode="local_only")
    eng_b = _make_engagement(name="Cloud Eng", privacy_mode="cloud_enabled")
    rows = [(eng_a, "owner"), (eng_b, "member")]

    with patch(
        "app.features.engagements.service.repo.list_engagements_for_user",
        new=AsyncMock(return_value=rows),
    ):
        result = await service.list_engagements(db, caller)

    assert len(result) == 2
    assert result[0].privacy_mode == "local_only"
    assert result[1].privacy_mode == "cloud_enabled"


# ---------------------------------------------------------------------------
# paused field — Slice 06 Task 2
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_engagement_paused_defaults_false(db: AsyncMock, caller: MagicMock) -> None:
    """create_engagement returns EngagementDetail with paused=False by default."""
    mock_eng = _make_engagement(paused=False)
    data = EngagementCreate(name="New Eng", scope="10.0.0.0/8")

    with patch(
        "app.features.engagements.service.repo.create_engagement",
        new=AsyncMock(return_value=mock_eng),
    ):
        result = await service.create_engagement(db, caller, data)

    assert result.paused is False


@pytest.mark.asyncio
async def test_get_engagement_paused_surfaces_in_detail(db: AsyncMock, caller: MagicMock) -> None:
    """get_engagement surfaces the paused field in EngagementDetail."""
    eng_id = uuid4()
    mock_eng = _make_engagement(engagement_id=eng_id, paused=True)
    mock_membership = _make_member(engagement_id=eng_id, user_id=caller.id, role="member")

    with patch(
        "app.features.engagements.service.repo.get_engagement_for_member",
        new=AsyncMock(return_value=(mock_eng, mock_membership)),
    ):
        result = await service.get_engagement(db, caller, eng_id)

    assert result.paused is True


@pytest.mark.asyncio
async def test_get_engagement_paused_false_surfaces_in_detail(
    db: AsyncMock, caller: MagicMock
) -> None:
    """get_engagement surfaces paused=False when the engagement is not paused."""
    eng_id = uuid4()
    mock_eng = _make_engagement(engagement_id=eng_id, paused=False)
    mock_membership = _make_member(engagement_id=eng_id, user_id=caller.id, role="owner")

    with patch(
        "app.features.engagements.service.repo.get_engagement_for_member",
        new=AsyncMock(return_value=(mock_eng, mock_membership)),
    ):
        result = await service.get_engagement(db, caller, eng_id)

    assert result.paused is False


@pytest.mark.asyncio
async def test_list_engagements_paused_surfaces_in_summary(
    db: AsyncMock, caller: MagicMock
) -> None:
    """list_engagements surfaces the paused field in each EngagementSummary."""
    eng_a = _make_engagement(name="Unpaused", paused=False)
    eng_b = _make_engagement(name="Paused", paused=True)
    rows = [(eng_a, "owner"), (eng_b, "member")]

    with patch(
        "app.features.engagements.service.repo.list_engagements_for_user",
        new=AsyncMock(return_value=rows),
    ):
        result = await service.list_engagements(db, caller)

    assert result[0].paused is False
    assert result[1].paused is True
