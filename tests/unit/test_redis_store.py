from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import fakeredis.aioredis
import pytest
from redis.asyncio import Redis

from agentkit.core.types import Role
from agentkit.llm.base import Message
from agentkit.memory.redis_store import RedisMemoryStore
from agentkit.tools.models import ToolCall


@pytest.fixture
async def fake_redis() -> AsyncIterator[Redis[Any]]:
    """Create a clean in-memory fake async Redis client."""
    client: Redis[Any] = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    aclose_fn = getattr(client, "aclose", None)
    if callable(aclose_fn):
        await aclose_fn()
    else:
        await client.close()


@pytest.mark.asyncio
async def test_redis_memory_store_get_empty_session(fake_redis: Redis[Any]) -> None:
    store = RedisMemoryStore(client=fake_redis, ttl_s=3600)
    messages = await store.get_messages("non_existent_session")
    assert messages == []


@pytest.mark.asyncio
async def test_redis_memory_store_save_and_get(fake_redis: Redis[Any]) -> None:
    store = RedisMemoryStore(client=fake_redis, ttl_s=1800)
    session_id = "session_1"

    msgs = [
        Message.system("You are a helpful assistant."),
        Message.user("Hello! Can you help me?"),
        Message.assistant(
            content="Yes, I can!",
            tool_calls=[ToolCall(id="c1", name="search", arguments={"query": "test"})],
        ),
        Message.tool_result(tool_call_id="c1", content="search results"),
    ]

    await store.save_messages(session_id, msgs)

    # Verify TTL was set
    ttl = await fake_redis.ttl(f"session:{session_id}:messages")
    assert 0 < ttl <= 1800

    # Retrieve and verify roundtrip serialization
    loaded = await store.get_messages(session_id)
    assert len(loaded) == 4
    assert loaded[0].role == Role.SYSTEM
    assert loaded[0].content == "You are a helpful assistant."

    assert loaded[1].role == Role.USER
    assert loaded[1].content == "Hello! Can you help me?"

    assert loaded[2].role == Role.ASSISTANT
    assert loaded[2].content == "Yes, I can!"
    assert loaded[2].tool_calls is not None
    assert len(loaded[2].tool_calls) == 1
    assert loaded[2].tool_calls[0].id == "c1"
    assert loaded[2].tool_calls[0].name == "search"
    assert loaded[2].tool_calls[0].arguments == {"query": "test"}

    assert loaded[3].role == Role.TOOL
    assert loaded[3].content == "search results"
    assert loaded[3].tool_call_id == "c1"


@pytest.mark.asyncio
async def test_redis_memory_store_clear_session(fake_redis: Redis[Any]) -> None:
    store = RedisMemoryStore(client=fake_redis, ttl_s=3600)
    session_id = "session_to_clear"

    await store.save_messages(session_id, [Message.user("Hi")])
    assert len(await store.get_messages(session_id)) == 1

    await store.clear_session(session_id)
    assert await store.get_messages(session_id) == []
    exists = await fake_redis.exists(f"session:{session_id}:messages")
    assert exists == 0


@pytest.mark.asyncio
async def test_sliding_window_truncation_with_system_prompt(fake_redis: Redis[Any]) -> None:
    # Set max_messages=3
    store = RedisMemoryStore(client=fake_redis, ttl_s=3600, max_messages=3)
    session_id = "session_trunc"

    messages = [
        Message.system("System prompt instructions"),
        Message.user("Message 1"),
        Message.assistant("Response 1"),
        Message.user("Message 2"),
        Message.assistant("Response 2"),
        Message.user("Message 3"),
    ]

    await store.save_messages(session_id, messages)

    loaded = await store.get_messages(session_id)
    # Total messages should be: 1 system prompt + 3 most recent messages = 4
    assert len(loaded) == 4
    assert loaded[0].role == Role.SYSTEM
    assert loaded[0].content == "System prompt instructions"
    assert loaded[1].content == "Message 2"
    assert loaded[2].content == "Response 2"
    assert loaded[3].content == "Message 3"


@pytest.mark.asyncio
async def test_sliding_window_truncation_without_system_prompt(fake_redis: Redis[Any]) -> None:
    # Set max_messages=2
    store = RedisMemoryStore(client=fake_redis, ttl_s=3600, max_messages=2)
    session_id = "session_no_sys"

    messages = [
        Message.user("Message 1"),
        Message.assistant("Response 1"),
        Message.user("Message 2"),
        Message.assistant("Response 2"),
    ]

    await store.save_messages(session_id, messages)

    loaded = await store.get_messages(session_id)
    assert len(loaded) == 2
    assert loaded[0].content == "Message 2"
    assert loaded[1].content == "Response 2"
