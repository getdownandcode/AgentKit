"""Redis session-store and rate-limiter tests executed against a live Redis server.

fakeredis is a pure-Python reimplementation; it does not exercise the hiredis
codec, real connection pooling, or genuine TTL expiry. These tests do.
"""

from __future__ import annotations

import asyncio

import pytest
from redis.asyncio import Redis

from agentkit.api.ratelimit import RateLimiter
from agentkit.core.errors import RateLimitExceededError
from agentkit.core.types import Role
from agentkit.llm.base import Message
from agentkit.memory.redis_store import RedisMemoryStore
from agentkit.tools.models import ToolCall

pytestmark = pytest.mark.real_infra


async def test_session_round_trip_through_real_redis(real_redis: Redis[str]) -> None:
    """Messages must survive JSON encoding and the hiredis codec intact."""
    store = RedisMemoryStore(client=real_redis, ttl_s=300)
    messages = [
        Message.system("You are a helpful assistant."),
        Message.user("Hello!"),
        Message.assistant(
            content="Hi!",
            tool_calls=[ToolCall(id="c1", name="calculator", arguments={"expression": "1+1"})],
        ),
        Message.tool_result(tool_call_id="c1", content="2"),
    ]

    await store.save_messages("sess-real", messages)
    loaded = await store.get_messages("sess-real")

    assert len(loaded) == 4
    assert loaded[0].role == Role.SYSTEM
    assert loaded[2].tool_calls is not None
    assert loaded[2].tool_calls[0].arguments == {"expression": "1+1"}
    assert loaded[3].role == Role.TOOL
    assert loaded[3].tool_call_id == "c1"


async def test_ttl_is_applied_by_real_server(real_redis: Redis[str]) -> None:
    """A short TTL must be visible through the server's own TTL command."""
    store = RedisMemoryStore(client=real_redis, ttl_s=5)
    await store.save_messages("sess-ttl", [Message.user("hi")])

    ttl = await real_redis.ttl("session:sess-ttl:messages")
    assert 0 < ttl <= 5


async def test_keys_expire_on_real_server(real_redis: Redis[str]) -> None:
    """Keys must genuinely disappear once the TTL elapses."""
    store = RedisMemoryStore(client=real_redis, ttl_s=1)
    await store.save_messages("sess-expiry", [Message.user("bye")])
    assert await store.get_messages("sess-expiry")

    await asyncio.sleep(1.5)
    assert await real_redis.exists("session:sess-expiry:messages") == 0
    assert await store.get_messages("sess-expiry") == []


async def test_sliding_window_truncation_is_persisted(real_redis: Redis[str]) -> None:
    """Truncation must be applied on write, not only on read."""
    store = RedisMemoryStore(client=real_redis, ttl_s=300, max_messages=3)
    await store.save_messages(
        "sess-truncate",
        [
            Message.system("System"),
            Message.user("one"),
            Message.assistant("two"),
            Message.user("three"),
            Message.assistant("four"),
            Message.user("five"),
        ],
    )

    loaded = await store.get_messages("sess-truncate")
    assert len(loaded) == 4
    assert loaded[0].content == "System"
    assert loaded[-1].content == "five"

    raw = await real_redis.get("session:sess-truncate:messages")
    assert raw is not None
    assert len(raw) < 400


async def test_clear_session_deletes_key(real_redis: Redis[str]) -> None:
    """clear_session must remove the key from the real server."""
    store = RedisMemoryStore(client=real_redis, ttl_s=300)
    await store.save_messages("sess-clear", [Message.user("x")])
    await store.clear_session("sess-clear")

    assert await real_redis.exists("session:sess-clear:messages") == 0


async def test_corrupt_payload_degrades_gracefully(real_redis: Redis[str]) -> None:
    """A non-JSON value must not raise out of the read path."""
    store = RedisMemoryStore(client=real_redis, ttl_s=300)
    await real_redis.set("session:sess-corrupt:messages", "{not-json")

    assert await store.get_messages("sess-corrupt") == []


async def test_rate_limiter_blocks_at_limit(real_redis: Redis[str]) -> None:
    """The ZSET sliding window must reject the (limit + 1)-th request."""
    limiter = RateLimiter(requests_per_window=3, window_seconds=60)
    key = "ak_ratelimit_probe"

    for _ in range(3):
        await limiter.check(key=key, redis_client=real_redis, limit=3)

    with pytest.raises(RateLimitExceededError) as excinfo:
        await limiter.check(key=key, redis_client=real_redis, limit=3)
    assert excinfo.value.retry_after >= 1

    card = await real_redis.zcard(f"ratelimit:{key}")
    assert card == 3


async def test_rate_limiter_window_expiry(real_redis: Redis[str]) -> None:
    """Once the window slides past, requests must be admitted again."""
    limiter = RateLimiter(requests_per_window=2, window_seconds=1)
    key = "ak_ratelimit_window"

    await limiter.check(key=key, redis_client=real_redis, limit=2)
    await limiter.check(key=key, redis_client=real_redis, limit=2)
    with pytest.raises(RateLimitExceededError):
        await limiter.check(key=key, redis_client=real_redis, limit=2)

    await asyncio.sleep(1.2)
    await limiter.check(key=key, redis_client=real_redis, limit=2)


async def test_rate_limiter_isolates_keys(real_redis: Redis[str]) -> None:
    """One client exhausting its quota must not affect another."""
    limiter = RateLimiter(requests_per_window=1, window_seconds=60)

    await limiter.check(key="ak_first", redis_client=real_redis, limit=1)
    with pytest.raises(RateLimitExceededError):
        await limiter.check(key="ak_first", redis_client=real_redis, limit=1)

    await limiter.check(key="ak_second", redis_client=real_redis, limit=1)


async def test_concurrent_checks_respect_limit(real_redis: Redis[str]) -> None:
    """Concurrent pipelined checks must not overshoot the configured limit."""
    limiter = RateLimiter(requests_per_window=5, window_seconds=60)
    key = "ak_ratelimit_concurrent"

    outcomes = await asyncio.gather(
        *(limiter.check(key=key, redis_client=real_redis, limit=5) for _ in range(20)),
        return_exceptions=True,
    )
    admitted = sum(1 for o in outcomes if not isinstance(o, BaseException))
    rejected = sum(1 for o in outcomes if isinstance(o, RateLimitExceededError))

    assert admitted == 5
    assert rejected == 15
    assert await real_redis.zcard(f"ratelimit:{key}") == 5
