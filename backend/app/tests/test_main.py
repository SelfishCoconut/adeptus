"""Structural tests for app/main.py — no HTTP calls, no DB, no env vars needed."""

from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.main import app


def test_app_is_fastapi_instance() -> None:
    """The module-level app object must be a FastAPI instance."""
    assert isinstance(app, FastAPI)


def test_health_route_registered() -> None:
    """The /api/v1/health route must be included in the app."""
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    assert "/api/v1/health" in paths


def test_auth_routes_registered() -> None:
    """The /api/v1/auth/login route must be included in the app."""
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    assert "/api/v1/auth/login" in paths


def test_personas_routes_registered() -> None:
    """The /api/v1/personas routes must be included in the app (Slice 15)."""
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    assert "/api/v1/personas" in paths
    assert "/api/v1/personas/{persona_id}" in paths
