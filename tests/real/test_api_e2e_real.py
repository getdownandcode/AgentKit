"""End-to-end API test against live PostgreSQL and Redis.

Unlike the hermetic API integration tests, this one runs the real FastAPI
lifespan, so the engine, session factory and Redis pool are constructed exactly
as they are in the Docker Compose service. The only override is the LLM, which
AGENTS.md requires be faked.

Note on event loops: ``TestClient`` drives the application on its own event loop,
so anything bound to the pytest loop (the engine and Redis fixtures) must only be
awaited from the test body, never from inside a request.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from agentkit.api.main import create_app
from agentkit.config import Settings
from agentkit.llm.fake import FakeLLMClient
from tests.conftest import database_test_url, redis_test_url

pytestmark = pytest.mark.real_infra

API_KEY = "ak_real_infra_key"


def _build_settings() -> Settings:
    """Settings pointing the app at the live infrastructure under test."""
    return Settings(
        DATABASE_URL=database_test_url(),
        REDIS_URL=redis_test_url(),
        API_KEYS=API_KEY,
        RATE_LIMIT_PER_MIN=1000,
        LLM_PROVIDER="fake",
        MAX_STEPS=5,
        RUN_TIMEOUT_S=30,
        SESSION_TTL_S=3600,
    )


def _scripted_llm() -> FakeLLMClient:
    """Fake LLM performing one calculator step, then answering."""
    llm = FakeLLMClient()
    llm.queue_tool_call(
        tool_name="calculator",
        arguments={"expression": "6 * 7"},
        call_id="call_real_1",
        text="Multiplying.",
    )
    llm.queue_text("The answer is 42.")
    return llm


@pytest.fixture
def real_client(
    real_engine: AsyncEngine,
    real_redis: Redis[str],
) -> Iterator[tuple[TestClient, FakeLLMClient]]:
    """TestClient whose lifespan owns live PostgreSQL and Redis connections.

    The lifespan opens its own engine and Redis client against the same
    endpoints, which is precisely the production code path under test. The
    fixtures are requested only to guarantee truncation and key cleanup happen
    before the app starts.
    """
    del real_engine, real_redis

    app = create_app(settings=_build_settings())
    llm = _scripted_llm()
    app.state.llm_client = llm

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, llm


@pytest.fixture
def client(real_client: tuple[TestClient, FakeLLMClient]) -> TestClient:
    """The TestClient half of the real_client fixture."""
    return real_client[0]


@pytest.fixture
def llm(real_client: tuple[TestClient, FakeLLMClient]) -> FakeLLMClient:
    """The scripted LLM half of the real_client fixture."""
    return real_client[1]


async def test_health_reports_live_dependencies(client: TestClient) -> None:
    """The health endpoint must probe the real engine and Redis pool."""
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["database"] == "healthy"
    assert body["redis"] == "healthy"


async def test_run_persists_run_and_steps_to_postgres(
    client: TestClient,
    real_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """POST /runs must write run and step rows through the real Postgres stores."""
    response = client.post(
        "/runs",
        json={"goal": "What is 6 times 7?"},
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "The answer is 42."
    assert payload["status"] == "succeeded"
    run_uuid = uuid.UUID(payload["run_id"])

    async with real_session_factory() as session:
        run_row = (
            await session.execute(
                text("SELECT goal, status FROM runs WHERE id = :rid"),
                {"rid": run_uuid},
            )
        ).one_or_none()
        assert run_row is not None
        assert run_row.goal == "What is 6 times 7?"
        assert run_row.status == "succeeded"

        step_count: int = (
            await session.execute(
                text("SELECT count(*) FROM steps WHERE run_id = :rid"),
                {"rid": run_uuid},
            )
        ).scalar_one()
        assert step_count >= 1

    trace = client.get(f"/runs/{payload['run_id']}/trace", headers={"X-API-Key": API_KEY})
    assert trace.status_code == 200
    assert any(s["tool_name"] == "calculator" for s in trace.json()["steps"])


async def test_session_history_persists_in_real_redis(
    client: TestClient,
    real_redis: Redis[str],
) -> None:
    """A session_id must cause conversation history to be written to live Redis."""
    session_id = "real-session-42"
    response = client.post(
        "/runs",
        json={"goal": "My name is Alice.", "session_id": session_id},
        headers={"X-API-Key": API_KEY},
    )
    assert response.status_code == 200

    key = f"session:{session_id}:messages"
    assert await real_redis.exists(key) == 1
    assert 0 < await real_redis.ttl(key) <= 3600


async def test_multi_turn_session_reuses_history(client: TestClient, llm: FakeLLMClient) -> None:
    """Second turn in the same session must receive the first turn's messages."""
    session_id = "real-session-multiturn"
    headers = {"X-API-Key": API_KEY}

    llm.queue_text("Noted, Alice.")
    llm.queue_text("Your name is Alice.")

    client.post(
        "/runs",
        json={"goal": "My name is Alice.", "session_id": session_id},
        headers=headers,
    )
    client.post(
        "/runs",
        json={"goal": "What is my name?", "session_id": session_id},
        headers=headers,
    )

    assert llm.call_count >= 3
    # The final call belongs to turn 2 and must carry turn 1's history.
    contents = [m.content for m in llm.history[-1]]
    assert "My name is Alice." in contents
    assert "What is my name?" in contents


async def test_run_details_and_missing_run(client: TestClient) -> None:
    """Retrieving a persisted run works; an unknown run yields 404."""
    headers = {"X-API-Key": API_KEY}

    created = client.post("/runs", json={"goal": "Persist me."}, headers=headers)
    assert created.status_code == 200
    run_id = created.json()["run_id"]

    fetched = client.get(f"/runs/{run_id}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["goal"] == "Persist me."

    assert client.get(f"/runs/{uuid.uuid4()}", headers=headers).status_code == 404


async def test_tools_endpoint_exposes_builtin_registry(client: TestClient) -> None:
    """The built-in tool registry is served from the real dependency graph."""
    response = client.get("/tools", headers={"X-API-Key": API_KEY})

    assert response.status_code == 200
    names = {t["name"] for t in response.json()["tools"]}
    assert {"calculator", "read_file", "http_fetch", "sql_readonly", "web_search"} <= names


async def test_unauthorized_request_is_rejected(client: TestClient) -> None:
    """Auth must be enforced before any store access."""
    assert client.post("/runs", json={"goal": "nope"}).status_code == 401
