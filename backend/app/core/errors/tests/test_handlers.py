from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.core.errors import (
    AuthenticationError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
    register_error_handlers,
)


def build_test_app() -> FastAPI:
    app = FastAPI()

    @app.get("/not-found")
    async def route_not_found() -> None:
        raise NotFoundError("thing not found")

    @app.get("/auth")
    async def route_auth() -> None:
        raise AuthenticationError("must log in")

    @app.get("/forbidden")
    async def route_forbidden() -> None:
        raise ForbiddenError("no access")

    @app.get("/domain-validation")
    async def route_domain_validation() -> None:
        raise ValidationError("bad value")

    class Body(BaseModel):
        count: int

    @app.post("/typed-body")
    async def route_typed_body(body: Body) -> dict[str, int]:
        return {"count": body.count}

    register_error_handlers(app)
    return app


@pytest.fixture()
async def client() -> AsyncGenerator[AsyncClient, None]:
    app = build_test_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_not_found_returns_404_with_envelope(client: AsyncClient) -> None:
    response = await client.get("/not-found")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["message"] == "thing not found"


async def test_authentication_error_returns_401_with_www_authenticate_header(
    client: AsyncClient,
) -> None:
    response = await client.get("/auth")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "authentication_error"
    assert body["error"]["message"] == "must log in"
    assert response.headers.get("www-authenticate") == "Cookie"


async def test_forbidden_returns_403(client: AsyncClient) -> None:
    response = await client.get("/forbidden")
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "forbidden"
    assert body["error"]["message"] == "no access"


async def test_domain_validation_error_returns_400(client: AsyncClient) -> None:
    response = await client.get("/domain-validation")
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"] == "bad value"


async def test_request_validation_error_returns_422_with_details(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/typed-body",
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert "details" in body["error"]
    assert isinstance(body["error"]["details"], list)
