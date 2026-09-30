"""Integration test verifying that conversation session memory persists across multiple runs with the same session_id."""

from __future__ import annotations

from typing import Any

import pytest
from fakeredis.aioredis import FakeRedis
from fastapi.testclient import TestClient
from redis.asyncio import Redis

from agentkit.api.deps import get_agent, get_memory_store, get_redis_client
from agentkit.api.main import create_app
from agentkit.config import Settings
from agentkit.core.agent import Agent, AgentConfig
from agentkit.llm.base import LLMResponse, Message
from agentkit.llm.fake import FakeLLMClient
from agentkit.memory.base import InMemoryMemoryStore
from agentkit.memory.redis_store import RedisMemoryStore
from agentkit.tools.registry import ToolRegistry


class MemoryInspectingFakeLLM(FakeLLMClient):
    """Fake LLM that records received message histories for assertion inspection."""

    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        for resp in responses:
            self.queue_text(resp)
        self.received_histories: list[list[Message]] = []

    async def chat(
        self,
        messages: list[Message],
        tools: Any = None,
    ) -> LLMResponse:
        self.received_histories.append([m.model_copy() for m in messages])
        return await super().chat(messages, tools)


@pytest.mark.asyncio
async def test_session_memory_persists_across_runs_in_core_agent() -> None:
    """Verify Agent.run loads prior messages from session memory and persists new turns."""
    redis: Redis[Any] = FakeRedis(decode_responses=True)
    memory_store = RedisMemoryStore(client=redis, ttl_s=3600)
    registry = ToolRegistry()

    llm = MemoryInspectingFakeLLM(
        responses=[
            "Hello Alice! I have noted your favorite language is Python.",
            "Your favorite language is Python!",
        ]
    )

    agent = Agent(
        llm=llm,
        registry=registry,
        memory=memory_store,
        config=AgentConfig(system_prompt="You are a helpful assistant."),
    )

    session_id = "test-persistent-session-42"

    # Turn 1: User introduces name and preference
    res1 = await agent.run(
        goal="My name is Alice and my favorite language is Python.", session_id=session_id
    )
    assert res1.final_answer == "Hello Alice! I have noted your favorite language is Python."

    # Verify messages saved to Redis session store
    saved_messages = await memory_store.get_messages(session_id)
    assert len(saved_messages) >= 3  # System prompt, User message, Assistant response

    # Turn 2: User asks a follow-up question referencing turn 1
    res2 = await agent.run(goal="What is my favorite language?", session_id=session_id)
    assert res2.final_answer == "Your favorite language is Python!"

    # Verify that during Turn 2, LLM received the history containing Turn 1
    assert len(llm.received_histories) == 2
    turn2_messages = llm.received_histories[1]

    # Must contain Turn 1 user message and assistant answer
    user_goals = [m.content for m in turn2_messages if m.role == "user"]
    assert "My name is Alice and my favorite language is Python." in user_goals
    assert "What is my favorite language?" in user_goals

    close_fn = getattr(redis, "aclose", redis.close)
    await close_fn()


@pytest.mark.asyncio
async def test_session_memory_persists_across_api_runs() -> None:
    """Verify POST /runs with same session_id preserves conversation context across calls."""
    settings = Settings(API_KEYS="secret_test_key")
    app = create_app(settings=settings)

    redis: Redis[Any] = FakeRedis(decode_responses=True)
    session_store = RedisMemoryStore(client=redis, ttl_s=3600)
    run_store = InMemoryMemoryStore()

    # Import TieredMemoryStore to ensure API can combine session & run persistence
    from agentkit.memory.tiered import TieredMemoryStore

    tiered_store = TieredMemoryStore(session_store=session_store, run_store=run_store)

    llm = MemoryInspectingFakeLLM(
        responses=[
            "Passcode remembered: blue-falcon-42.",
            "Your passcode is blue-falcon-42.",
        ]
    )

    agent = Agent(
        llm=llm,
        registry=ToolRegistry(),
        memory=tiered_store,
        config=AgentConfig(system_prompt="Security Assistant"),
    )

    app.dependency_overrides[get_agent] = lambda: agent
    app.dependency_overrides[get_memory_store] = lambda: tiered_store
    app.dependency_overrides[get_redis_client] = lambda: redis

    client = TestClient(app, raise_server_exceptions=False)
    headers = {"X-API-Key": "secret_test_key"}
    session_id = "api_session_alpha"

    # API Call 1
    resp1 = client.post(
        "/runs",
        json={"goal": "The secret code is blue-falcon-42", "session_id": session_id},
        headers=headers,
    )
    assert resp1.status_code == 200
    assert resp1.json()["answer"] == "Passcode remembered: blue-falcon-42."

    # API Call 2 with identical session_id
    resp2 = client.post(
        "/runs",
        json={"goal": "What is the secret code?", "session_id": session_id},
        headers=headers,
    )
    assert resp2.status_code == 200
    assert resp2.json()["answer"] == "Your passcode is blue-falcon-42."

    # Check LLM saw the full multi-turn history on turn 2
    assert len(llm.received_histories) == 2
    turn2_contents = [m.content for m in llm.received_histories[1]]
    assert "The secret code is blue-falcon-42" in turn2_contents

    close_fn = getattr(redis, "aclose", redis.close)
    await close_fn()
