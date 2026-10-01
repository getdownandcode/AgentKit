"""Unit tests for FastAPI dependency resolution in agentkit.api.deps."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentkit.api.deps import get_db_session
from agentkit.api.main import create_app
from agentkit.config import Settings
from agentkit.core.errors import ServiceUnavailableError


def _app() -> FastAPI:
    """Build the real app plus a route that consumes a database session.

    Built through ``create_app`` so the domain exception handlers that map
    ``ServiceUnavailableError`` to 503 are actually registered.
    """
    application = create_app(settings=Settings(API_KEYS="k"))
    router = APIRouter()

    @router.get("/probe")
    async def probe(session: AsyncSession = Depends(get_db_session)) -> dict[str, bool]:
        return {"has_session": isinstance(session, AsyncSession)}

    application.include_router(router)
    return application


def test_get_db_session_returns_503_when_database_absent() -> None:
    """Without a session factory the dependency must fail explicitly, not silently.

    Yielding nothing would leave FastAPI's solver with an exhausted generator and
    surface as an opaque ``generator didn't yield`` RuntimeError.
    """
    client = TestClient(_app(), raise_server_exceptions=False)

    response = client.get("/probe")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


def test_get_db_session_yields_session_when_factory_present() -> None:
    """A configured session factory yields a usable AsyncSession."""
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        class_=AsyncSession, expire_on_commit=False
    )
    app = _app()
    app.state.session_factory = factory

    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/probe")

    assert response.status_code == 200
    assert response.json() == {"has_session": True}


async def test_get_db_session_raises_domain_error_directly() -> None:
    """Direct invocation raises the domain error rather than a framework error."""
    generator = get_db_session(factory=None)

    with pytest.raises(ServiceUnavailableError) as excinfo:
        await generator.__anext__()

    assert excinfo.value.service == "database"
    assert excinfo.value.code == "SERVICE_UNAVAILABLE"


async def test_service_unavailable_error_serializes() -> None:
    """The error carries a structured payload for API responses."""
    error = ServiceUnavailableError("redis", "Redis is unreachable.")

    payload: dict[str, Any] = error.to_dict()

    assert payload == {"code": "SERVICE_UNAVAILABLE", "message": "Redis is unreachable."}
