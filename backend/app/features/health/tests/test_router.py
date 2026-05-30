"""Unit tests for the health check endpoint."""

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.features.health.router import router

app = FastAPI()
app.include_router(router)


async def test_health_returns_ok() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["version"], str) and body["version"]
