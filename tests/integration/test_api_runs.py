import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentkit.api.deps import get_agent, get_llm_client, get_memory_store, get_trace_sink
from agentkit.api.main import create_app
from agentkit.config import Settings
from agentkit.core.agent import Agent, AgentConfig
from agentkit.core.pg_trace import PostgresTraceSink
from agentkit.core.trace import InMemoryTraceSink
from agentkit.db.models import Base
from agentkit.llm.base import LLMResponse
from agentkit.llm.fake import FakeLLMClient
from agentkit.memory.base import InMemoryMemoryStore
from agentkit.memory.pg_store import PostgresMemoryStore
from agentkit.tools.models import ToolCall
from agentkit.tools.registry import ToolRegistry


@pytest.fixture
async def async_db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield session_factory
    await engine.dispose()


def test_runs_auth_required() -> None:
    settings = Settings(API_KEYS="secret_token")
    app = create_app(settings=settings)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post("/runs", json={"goal": "calculate 2+2"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"

    resp = client.get(f"/runs/{uuid.uuid4()}")
    assert resp.status_code == 401

    resp = client.get(f"/runs/{uuid.uuid4()}/trace")
    assert resp.status_code == 401


def test_runs_validation_error() -> None:
    settings = Settings(API_KEYS="secret_token")
    app = create_app(settings=settings)
    app.dependency_overrides[get_memory_store] = lambda: InMemoryMemoryStore()
    app.dependency_overrides[get_trace_sink] = lambda: InMemoryTraceSink()
    app.dependency_overrides[get_llm_client] = lambda: FakeLLMClient()
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"X-API-Key": "secret_token"}

    # Missing goal
    resp = client.post("/runs", json={}, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    # Empty goal
    resp = client.post("/runs", json={"goal": ""}, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_create_and_query_run_lifecycle(
    async_db: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(API_KEYS="secret_token")
    app = create_app(settings=settings)
    app.state.session_factory = async_db
    memory_store = PostgresMemoryStore(async_db)

    # Setup fake agent
    registry = ToolRegistry()

    def calc(expr: str) -> int:
        return 42 if expr else 0

    registry.register(calc, name="calc", description="calculator")

    fake_llm = FakeLLMClient(
        responses=[
            LLMResponse(
                tool_calls=[ToolCall(id="call_1", name="calc", arguments={"expr": "6*7"})],
            ),
            LLMResponse(
                text="The result is 42.",
            ),
        ]
    )

    trace_sink = PostgresTraceSink(async_db)

    agent = Agent(
        llm=fake_llm,
        registry=registry,
        memory=memory_store,
        trace=trace_sink,
        config=AgentConfig(max_steps=5),
    )

    app.dependency_overrides[get_agent] = lambda: agent
    app.dependency_overrides[get_memory_store] = lambda: memory_store

    client = TestClient(app, raise_server_exceptions=False)
    headers = {"X-API-Key": "secret_token"}

    # 1. POST /runs
    resp_create = client.post(
        "/runs",
        json={"goal": "calculate 6*7", "session_id": "test_session_1"},
        headers=headers,
    )
    assert resp_create.status_code == 200
    create_data = resp_create.json()
    assert "run_id" in create_data
    assert create_data["status"] == "succeeded"
    assert create_data["answer"] == "The result is 42."
    assert create_data["session_id"] == "test_session_1"
    run_id = create_data["run_id"]

    # 2. GET /runs/{run_id}
    resp_get = client.get(f"/runs/{run_id}", headers=headers)
    assert resp_get.status_code == 200
    get_data = resp_get.json()
    assert get_data["run_id"] == run_id
    assert get_data["status"] == "succeeded"
    assert get_data["answer"] == "The result is 42."
    assert get_data["goal"] == "calculate 6*7"

    # 3. GET /runs/{run_id}/trace
    resp_trace = client.get(f"/runs/{run_id}/trace", headers=headers)
    assert resp_trace.status_code == 200
    trace_data = resp_trace.json()
    assert trace_data["run_id"] == run_id
    assert len(trace_data["steps"]) >= 1
    assert trace_data["steps"][0]["tool_name"] == "calc"
    assert trace_data["steps"][0]["args"] == {"expr": "6*7"}


def test_get_nonexistent_run() -> None:
    settings = Settings(API_KEYS="secret_token")
    app = create_app(settings=settings)
    memory_store = InMemoryMemoryStore()
    app.dependency_overrides[get_memory_store] = lambda: memory_store
    app.dependency_overrides[get_trace_sink] = lambda: InMemoryTraceSink()

    client = TestClient(app, raise_server_exceptions=False)
    headers = {"X-API-Key": "secret_token"}

    missing_id = str(uuid.uuid4())
    resp = client.get(f"/runs/{missing_id}", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RUN_NOT_FOUND"

    resp_trace = client.get(f"/runs/{missing_id}/trace", headers=headers)
    assert resp_trace.status_code == 404
    assert resp_trace.json()["error"]["code"] == "RUN_NOT_FOUND"
