from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentkit.api.main import create_app
from agentkit.config import Settings
from agentkit.tools.builtin.calculator import calculator
from agentkit.tools.registry import ToolRegistry


def test_health_public_access_no_auth() -> None:
    settings = Settings(API_KEYS="secret_token")
    app = create_app(settings=settings)
    client = TestClient(app, raise_server_exceptions=False)

    # Health check must not require X-API-Key header
    resp = client.get("/health")
    assert resp.status_code in (200, 503)


@pytest.mark.asyncio
async def test_health_healthy_stack() -> None:
    settings = Settings(API_KEYS="secret_token")
    app = create_app(settings=settings)

    # Attach in-memory DB and fake Redis
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession)
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    app.state.db_engine = engine
    app.state.session_factory = session_factory
    app.state.redis_client = fake_redis

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["database"] == "healthy"
    assert data["redis"] == "healthy"

    await engine.dispose()
    aclose_fn = getattr(fake_redis, "aclose", fake_redis.close)
    await aclose_fn()


@pytest.mark.asyncio
async def test_health_unhealthy_when_db_down() -> None:
    settings = Settings(API_KEYS="secret_token")
    app = create_app(settings=settings)

    # Attach broken DB mock
    conn_mock = AsyncMock()
    conn_mock.__aenter__.side_effect = ConnectionRefusedError("Database unreachable")
    broken_engine = MagicMock()
    broken_engine.connect.return_value = conn_mock
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    app.state.db_engine = broken_engine
    app.state.redis_client = fake_redis

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/health")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "unhealthy"
    assert data["database"] == "unhealthy"
    assert data["redis"] == "healthy"

    aclose_fn = getattr(fake_redis, "aclose", fake_redis.close)
    await aclose_fn()


def test_tools_auth_required() -> None:
    settings = Settings(API_KEYS="secret_token")
    app = create_app(settings=settings)
    client = TestClient(app, raise_server_exceptions=False)

    # Missing auth returns 401
    resp = client.get("/tools")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


def test_tools_discovery_listing() -> None:
    settings = Settings(API_KEYS="secret_token")
    app = create_app(settings=settings)

    registry = ToolRegistry()
    registry.register(calculator)
    app.state.tool_registry = registry

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/tools", headers={"X-API-Key": "secret_token"})
    assert resp.status_code == 200
    data = resp.json()
    assert "tools" in data
    tool_names = [t["name"] for t in data["tools"]]
    assert "calculator" in tool_names

    calc_entry = next(t for t in data["tools"] if t["name"] == "calculator")
    assert "parameters" in calc_entry
    assert "description" in calc_entry
