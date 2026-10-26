"""Regression tests for session history persistence (AUDIT-04, AUDIT-10, AUDIT-11).

Three separate ways a session could lose its context: a binary signature that made the whole
save fail, a corrupt payload that was silently read as an empty history, and a truncation that
orphaned tool results. Each is pinned here.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast

import fakeredis.aioredis
import pytest
from redis.asyncio import Redis

from agentkit.api.deps import get_memory_store
from agentkit.config import Settings
from agentkit.core.agent import Agent
from agentkit.core.errors import SessionHistoryError
from agentkit.core.types import Role, RunStatus
from agentkit.llm.base import LLMResponse, Message
from agentkit.llm.fake import FakeLLMClient
from agentkit.memory.redis_store import RedisMemoryStore
from agentkit.memory.tiered import TieredMemoryStore
from agentkit.tools.models import THOUGHT_SIGNATURE_BASE64_KEY, ToolCall
from agentkit.tools.registry import ToolRegistry


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


def _assistant_with_signature(signature: bytes | None) -> Message:
    return Message.assistant(
        content="",
        tool_calls=[
            ToolCall(
                id="call-1",
                name="calculator",
                arguments={"expression": "1+1"},
                thought_signature=signature,
            )
        ],
    )


@pytest.mark.parametrize(
    "signature",
    [
        b"\xff\xfe\x00\x81",
        b"\x00",
        b"",
        b"c2VjcmV0",
        bytes(range(256)),
    ],
)
@pytest.mark.asyncio
async def test_thought_signature_round_trips_byte_for_byte(
    fake_redis: Redis[Any], signature: bytes
) -> None:
    """Pydantic's default bytes handling cannot round-trip arbitrary binary."""
    store = RedisMemoryStore(client=fake_redis, ttl_s=3600)
    messages = [Message.user("hi"), _assistant_with_signature(signature)]

    await store.save_messages("session_sig", messages)
    loaded = await store.get_messages("session_sig")

    assert loaded[1].tool_calls[0].thought_signature == signature


@pytest.mark.asyncio
async def test_persisted_signature_uses_an_explicit_tagged_encoding(
    fake_redis: Redis[Any],
) -> None:
    """The stored form must not depend on Pydantic's JSON defaults."""
    store = RedisMemoryStore(client=fake_redis, ttl_s=3600)
    signature = b"\xff\xfe\x00\x81"

    await store.save_messages("session_tagged", [_assistant_with_signature(signature)])

    raw = await fake_redis.get("session:session_tagged:messages")
    assert raw is not None
    stored = json.loads(raw)
    encoded = stored[0]["tool_calls"][0]["thought_signature"]
    assert encoded == {THOUGHT_SIGNATURE_BASE64_KEY: base64.b64encode(signature).decode("ascii")}


@pytest.mark.asyncio
async def test_legacy_utf8_signature_is_still_readable(fake_redis: Redis[Any]) -> None:
    """Histories written before the codec stored the raw bytes' UTF-8 decoding."""
    legacy = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-legacy",
                    "name": "calculator",
                    "arguments": {},
                    "thought_signature": "c2VjcmV0",
                }
            ],
        }
    ]
    await fake_redis.set("session:legacy:messages", json.dumps(legacy))

    store = RedisMemoryStore(client=fake_redis, ttl_s=3600)
    loaded = await store.get_messages("legacy")

    assert loaded[0].tool_calls[0].thought_signature == b"c2VjcmV0"


@pytest.mark.asyncio
async def test_corrupt_history_is_surfaced_not_read_as_empty(
    fake_redis: Redis[Any], caplog: pytest.LogCaptureFixture
) -> None:
    """Returning [] for an unreadable payload is what made corruption invisible."""
    store = RedisMemoryStore(client=fake_redis, ttl_s=3600)
    await fake_redis.set("session:corrupt:messages", "{not json")

    with (
        caplog.at_level(logging.ERROR, logger="agentkit.memory.redis_store"),
        pytest.raises(SessionHistoryError) as excinfo,
    ):
        await store.get_messages("corrupt")

    assert "corrupt" in str(excinfo.value)
    assert any("Corrupt session history" in record.message for record in caplog.records)
    assert await fake_redis.get("session:corrupt:messages") == "{not json"


class _ExplodingHistoryStore:
    """Store whose history cannot be read, but whose writes are recorded."""

    def __init__(self) -> None:
        self.saved: list[list[Message]] = []
        self.updates: list[RunStatus | str] = []

    async def get_messages(self, session_id: str) -> list[Message]:
        del session_id  # the point of this double is that the read fails
        raise RuntimeError("redis connection lost")

    async def save_messages(self, session_id: str, messages: list[Message]) -> None:
        del session_id
        self.saved.append(list(messages))

    async def create_run(self, run_id: str, goal: str, session_id: str | None = None) -> None:
        del run_id, goal, session_id
        return None

    async def update_run(self, run_id: str, status: RunStatus | str, **_: Any) -> None:
        del run_id
        self.updates.append(status)


@pytest.mark.asyncio
async def test_agent_refuses_to_run_on_a_history_load_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The run fails loudly and the stored history is never overwritten."""
    store = _ExplodingHistoryStore()
    agent = Agent(
        llm=FakeLLMClient(responses=[LLMResponse(text="unused")]),
        registry=ToolRegistry(),
        memory=store,
    )

    with (
        caplog.at_level(logging.ERROR, logger="agentkit.core.agent"),
        pytest.raises(SessionHistoryError),
    ):
        await agent.run(goal="continue our conversation", session_id="sess-lost")

    assert store.saved == [], "an unreadable history must not be replaced"
    assert any("Could not load session history" in record.message for record in caplog.records)
    assert store.updates == [RunStatus.FAILED], "the run must not be left running"


def _tool_call_ids(message: Message) -> set[str]:
    return {call.id for call in message.tool_calls}


def _is_provider_valid(messages: list[Message]) -> bool:
    """Every tool result must answer a call that is present earlier in the same history."""
    for index, message in enumerate(messages):
        if message.role == Role.TOOL:
            if index == 0:
                return False
            answered = {
                call_id for earlier in messages[:index] for call_id in _tool_call_ids(earlier)
            }
            if message.tool_call_id not in answered:
                return False
        elif message.role == Role.ASSISTANT and message.tool_calls:
            later_results = {
                result.tool_call_id for result in messages[index + 1 :] if result.role == Role.TOOL
            }
            if not _tool_call_ids(message) <= later_results:
                return False
    return True


def _tool_call_history() -> list[Message]:
    return [
        Message.system("system prompt"),
        Message.user("first question"),
        Message.assistant("", [ToolCall(id="c1", name="calculator", arguments={"x": 1})]),
        Message.tool_result("c1", "1"),
        Message.assistant("", [ToolCall(id="c2", name="calculator", arguments={"x": 2})]),
        Message.tool_result("c2", "2"),
    ]


@pytest.mark.parametrize("max_messages", list(range(1, 8)))
def test_truncation_never_orphans_a_tool_result(max_messages: int) -> None:
    """A naive tail cut starts with a tool message whose assistant turn was dropped."""
    messages = _tool_call_history()

    truncated = RedisMemoryStore.truncate_messages(messages, max_messages)

    assert len(truncated) <= max_messages
    assert _is_provider_valid(truncated), [m.role for m in truncated]
    if max_messages > 1:
        assert truncated[0].role == Role.SYSTEM, "the system prompt is always preserved"


def test_truncation_preserves_the_system_prompt_and_recent_turns() -> None:
    """The documented behaviour of the sliding window is unchanged."""
    messages = [Message.system("sys"), Message.user("u1"), Message.assistant("a1")]
    assert RedisMemoryStore.truncate_messages(messages, 2) == [messages[0], messages[2]]


@pytest.mark.asyncio
async def test_long_session_history_stays_within_the_configured_bound(
    fake_redis: Redis[Any],
) -> None:
    """Without a bound, session histories grow in Redis forever."""
    store = RedisMemoryStore(client=fake_redis, ttl_s=3600, max_messages=8)
    messages = [Message.system("sys")]
    for index in range(100):
        messages.append(Message.user(f"question {index}"))
        messages.append(Message.assistant(f"answer {index}"))

    await store.save_messages("session_bounded", messages)
    loaded = await store.get_messages("session_bounded")

    assert len(loaded) <= 8
    assert loaded[-1].content == "answer 99"


def test_session_max_messages_setting_reaches_the_redis_store(
    fake_redis: Redis[Any],
) -> None:
    """A setting that is never wired is dead configuration."""
    from starlette.requests import Request

    settings = Settings(API_KEYS="k", SESSION_MAX_MESSAGES=7)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings,
                redis_client=fake_redis,
                session_factory=object(),
            )
        )
    )

    store = get_memory_store(cast(Request, request))
    assert isinstance(store, TieredMemoryStore)

    session_store = store.session_store
    assert isinstance(session_store, RedisMemoryStore)
    assert session_store.max_messages == 7
