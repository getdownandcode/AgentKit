"""Regression tests for persistence integrity (AUDIT-03, AUDIT-08, AUDIT-09).

The failure modes here all shared one shape: a write to the run store failed, the error was
swallowed, and the client was handed a success for a run the database never stored.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentkit.api.deps import get_agent, get_memory_store
from agentkit.api.main import create_app
from agentkit.api.schemas import RunCreateRequest
from agentkit.config import Settings
from agentkit.core.agent import Agent
from agentkit.core.types import RunStatus
from agentkit.db.models import SESSION_ID_MAX_LENGTH, Base, Run
from agentkit.llm.base import LLMResponse
from agentkit.llm.fake import FakeLLMClient
from agentkit.memory.base import InMemoryMemoryStore
from agentkit.memory.pg_store import PostgresMemoryStore
from agentkit.tools.registry import ToolRegistry

HEADERS = {"X-API-Key": "secret_token"}


@pytest.fixture
async def async_db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield session_factory
    await engine.dispose()


def _build_client(store: Any) -> TestClient:
    """Create an app whose agent and run store are both backed by ``store``."""
    app = create_app(settings=Settings(API_KEYS="secret_token"))
    agent = Agent(
        llm=FakeLLMClient(responses=[LLMResponse(text="done")]),
        registry=ToolRegistry(),
        memory=store,
    )
    app.dependency_overrides[get_agent] = lambda: agent
    app.dependency_overrides[get_memory_store] = lambda: store
    return TestClient(app, raise_server_exceptions=False)


async def _stored_runs(session_factory: async_sessionmaker[AsyncSession]) -> list[Run]:
    async with session_factory() as session:
        return list((await session.execute(select(Run))).scalars().all())


class _CreateFailsStore(PostgresMemoryStore):
    """A store whose run creation always fails (database unreachable)."""

    async def create_run(self, run_id: str, goal: str, session_id: str | None = None) -> None:
        del run_id, goal, session_id  # signature must match the store being stood in for
        raise RuntimeError("connection refused")


class _VanishingStore(PostgresMemoryStore):
    """A store that accepts work but cannot read the run back."""

    async def get_run(self, run_id: str) -> None:
        del run_id  # signature must match the store being stood in for
        return None


class _FailFirstUpdateStore(PostgresMemoryStore):
    """A store whose first status update fails, then recovers."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        super().__init__(session_factory)
        self.calls = 0

    async def update_run(
        self,
        run_id: str,
        status: RunStatus | str,
        final_answer: str | None = None,
        failure_reason: str | None = None,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
    ) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("connection reset")
        await super().update_run(
            run_id=run_id,
            status=status,
            final_answer=final_answer,
            failure_reason=failure_reason,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
        )


async def test_create_run_failure_is_not_reported_as_success(
    async_db: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A run that was never persisted must not come back as HTTP 200 succeeded."""
    client = _build_client(_CreateFailsStore(async_db))

    with caplog.at_level(logging.ERROR, logger="agentkit.core.agent"):
        resp = client.post("/runs", json={"goal": "do something"}, headers=HEADERS)

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "PERSISTENCE_ERROR"
    assert await _stored_runs(async_db) == [], "no run row should exist"
    assert any("Could not create run" in record.message for record in caplog.records)


async def test_unreadable_run_is_not_synthesised_into_a_response(
    async_db: async_sessionmaker[AsyncSession],
) -> None:
    """If the finished run cannot be read back, the client must not receive a made-up 200."""
    client = _build_client(_VanishingStore(async_db))

    resp = client.post("/runs", json={"goal": "do something"}, headers=HEADERS)

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "PERSISTENCE_ERROR"


async def test_failed_status_update_marks_the_run_failed_instead_of_leaving_it_running(
    async_db: async_sessionmaker[AsyncSession],
) -> None:
    """A transient final-update failure leaves a truthful terminal state, not 'running'."""
    store = _FailFirstUpdateStore(async_db)
    client = _build_client(store)

    resp = client.post("/runs", json={"goal": "do something"}, headers=HEADERS)

    assert resp.status_code == 503
    runs = await _stored_runs(async_db)
    assert len(runs) == 1
    assert runs[0].status == RunStatus.FAILED.value, "a failed update must not stay 'running'"
    assert runs[0].failure_reason is not None
    assert store.calls >= 2, "the agent should retry once with a failed status"


async def test_persisted_run_response_matches_the_stored_record(
    async_db: async_sessionmaker[AsyncSession],
) -> None:
    """A successful run returns the stored goal, session and status, not a placeholder."""
    store = PostgresMemoryStore(async_db)
    client = _build_client(store)

    resp = client.post(
        "/runs",
        json={"goal": "the real goal", "session_id": "sess-1"},
        headers=HEADERS,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["goal"] == "the real goal"
    assert body["session_id"] == "sess-1"
    assert body["answer"] == "done"

    stored = await _stored_runs(async_db)
    assert len(stored) == 1
    assert stored[0].goal == "the real goal"
    assert stored[0].session_id == "sess-1"
    assert stored[0].status == RunStatus.SUCCEEDED.value


class _SpyStore(InMemoryMemoryStore):
    """In-memory store that records whether a run was ever created."""

    def __init__(self) -> None:
        super().__init__()
        self.created: list[str] = []

    async def create_run(self, run_id: str, goal: str, session_id: str | None = None) -> None:
        self.created.append(run_id)
        await super().create_run(run_id=run_id, goal=goal, session_id=session_id)


@pytest.mark.parametrize("length", [SESSION_ID_MAX_LENGTH + 1, SESSION_ID_MAX_LENGTH * 4])
def test_oversized_session_id_is_rejected_before_any_work(length: int) -> None:
    """A session id wider than the column is a 422, not a swallowed insert error."""
    store = _SpyStore()
    client = _build_client(store)

    resp = client.post("/runs", json={"goal": "x", "session_id": "s" * length}, headers=HEADERS)

    assert resp.status_code == 422
    assert store.created == [], "validation must stop the run before any persistence"


def test_session_id_at_the_limit_is_accepted() -> None:
    """A maximum-width id round-trips unchanged."""
    store = InMemoryMemoryStore()
    client = _build_client(store)
    session_id = "s" * SESSION_ID_MAX_LENGTH

    resp = client.post("/runs", json={"goal": "x", "session_id": session_id}, headers=HEADERS)

    assert resp.status_code == 200
    assert resp.json()["session_id"] == session_id


def _declared_max_length(prop: dict[str, Any]) -> int | None:
    """Return a property's maxLength, looking inside ``anyOf`` for optional fields."""
    branches = [prop, *prop.get("anyOf", [])]
    for branch in branches:
        if "maxLength" in branch:
            return int(branch["maxLength"])
    return None


def test_api_session_limit_matches_the_column_width() -> None:
    """The request boundary and the database column must not drift apart again."""
    schema = RunCreateRequest.model_json_schema()
    api_limit = _declared_max_length(schema["properties"]["session_id"])
    column_width = getattr(Run.__table__.c.session_id.type, "length", None)

    assert api_limit == column_width == SESSION_ID_MAX_LENGTH
