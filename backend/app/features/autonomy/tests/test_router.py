"""Router tests for the autonomy feature (Slice 18).

Exercise the real service + audit (SQLite) through the HTTP layer with a seeded member
user and engagement (``client`` fixture). Covers status codes + the membership 404.
"""

from uuid import UUID, uuid4

from httpx import AsyncClient

from app.features.auth.models import User

_GRANTS = "/api/v1/engagements/{eng}/autonomy-grants"


async def test_grant_returns_201_then_listed(client: tuple[AsyncClient, User, UUID]) -> None:
    ac, _user, eng = client
    resp = await ac.post(_GRANTS.format(eng=eng), json={"reason": "aggressive_scan"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["reason"] == "aggressive_scan"
    assert body["revoked_at"] is None
    assert body["granted_by_username"] == "alice"

    listed = await ac.get(_GRANTS.format(eng=eng))
    assert listed.status_code == 200
    assert [g["reason"] for g in listed.json()] == ["aggressive_scan"]


async def test_grant_duplicate_returns_409(client: tuple[AsyncClient, User, UUID]) -> None:
    ac, _user, eng = client
    assert (
        await ac.post(_GRANTS.format(eng=eng), json={"reason": "target_write"})
    ).status_code == 201
    dup = await ac.post(_GRANTS.format(eng=eng), json={"reason": "target_write"})
    assert dup.status_code == 409


async def test_grant_unclassified_manifest_returns_422(
    client: tuple[AsyncClient, User, UUID],
) -> None:
    ac, _user, eng = client
    resp = await ac.post(_GRANTS.format(eng=eng), json={"reason": "unclassified_manifest"})
    assert resp.status_code == 422


async def test_grant_unknown_reason_returns_422(client: tuple[AsyncClient, User, UUID]) -> None:
    ac, _user, eng = client
    resp = await ac.post(_GRANTS.format(eng=eng), json={"reason": "nope"})
    assert resp.status_code == 422


async def test_revoke_returns_204_then_gone(client: tuple[AsyncClient, User, UUID]) -> None:
    ac, _user, eng = client
    created = await ac.post(_GRANTS.format(eng=eng), json={"reason": "out_of_scope"})
    grant_id = created.json()["id"]

    revoked = await ac.delete(f"{_GRANTS.format(eng=eng)}/{grant_id}")
    assert revoked.status_code == 204

    listed = await ac.get(_GRANTS.format(eng=eng))
    assert listed.json() == []


async def test_revoke_unknown_returns_404(client: tuple[AsyncClient, User, UUID]) -> None:
    ac, _user, eng = client
    resp = await ac.delete(f"{_GRANTS.format(eng=eng)}/{uuid4()}")
    assert resp.status_code == 404


async def test_non_member_engagement_returns_404(client: tuple[AsyncClient, User, UUID]) -> None:
    ac, _user, _eng = client
    # An engagement the seeded user is not a member of → 404 (not 403), §17.1.
    other = uuid4()
    assert (await ac.get(_GRANTS.format(eng=other))).status_code == 404
    grant = await ac.post(_GRANTS.format(eng=other), json={"reason": "aggressive_scan"})
    assert grant.status_code == 404
