from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis

from agentkit.api.main import create_app
from agentkit.api.ratelimit import RateLimiter
from agentkit.config import Settings
from agentkit.core.errors import RateLimitExceededError


@pytest.fixture
async def fake_redis() -> AsyncIterator[Redis[Any]]:
    """Create an isolated fake async Redis instance for rate limit testing."""
    client: Redis[Any] = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    aclose_fn = getattr(client, "aclose", None)
    if callable(aclose_fn):
        await aclose_fn()
    else:
        await client.close()


@pytest.mark.asyncio
async def test_rate_limiter_allows_under_limit_redis(fake_redis: Redis[Any]) -> None:
    limiter = RateLimiter(requests_per_window=3, window_seconds=60)
    key = "ak_test_client_1"

    # 3 calls should succeed without error
    for _ in range(3):
        await limiter.check(key=key, redis_client=fake_redis)


@pytest.mark.asyncio
async def test_rate_limiter_trips_when_limit_exceeded_redis(fake_redis: Redis[Any]) -> None:
    limiter = RateLimiter(requests_per_window=2, window_seconds=60)
    key = "ak_test_client_2"

    await limiter.check(key=key, redis_client=fake_redis)
    await limiter.check(key=key, redis_client=fake_redis)

    with pytest.raises(RateLimitExceededError) as exc_info:
        await limiter.check(key=key, redis_client=fake_redis)

    assert exc_info.value.retry_after > 0
    assert "Rate limit exceeded" in exc_info.value.message


@pytest.mark.asyncio
async def test_rate_limiter_window_reset_redis(fake_redis: Redis[Any]) -> None:
    limiter = RateLimiter(requests_per_window=1, window_seconds=10)
    key = "ak_test_client_3"

    base_time = 1000.0
    with patch("time.time", return_value=base_time):
        await limiter.check(key=key, redis_client=fake_redis)
        with pytest.raises(RateLimitExceededError):
            await limiter.check(key=key, redis_client=fake_redis)

    # Advance time beyond window
    with patch("time.time", return_value=base_time + 11.0):
        # Should now succeed
        await limiter.check(key=key, redis_client=fake_redis)


@pytest.mark.asyncio
async def test_rate_limiter_in_memory_fallback() -> None:
    limiter = RateLimiter(requests_per_window=2, window_seconds=10)
    key = "ak_mem_key"

    base_time = 2000.0
    with patch("time.time", return_value=base_time):
        await limiter.check(key=key, redis_client=None)
        await limiter.check(key=key, redis_client=None)
        with pytest.raises(RateLimitExceededError) as exc_info:
            await limiter.check(key=key, redis_client=None)
        assert exc_info.value.retry_after > 0

    with patch("time.time", return_value=base_time + 11.0):
        await limiter.check(key=key, redis_client=None)


@pytest.mark.asyncio
async def test_rate_limit_api_endpoint_429_with_headers(fake_redis: Redis[Any]) -> None:
    test_settings = Settings(
        API_KEYS="test_api_key",
        RATE_LIMIT_PER_MIN=2,
    )
    app = create_app(settings=test_settings)
    app.state.redis_client = fake_redis

    transport = ASGITransport(app=app)
    headers = {"X-API-Key": "test_api_key"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Request 1: OK
        res1 = await client.get("/tools", headers=headers)
        assert res1.status_code == 200

        # Request 2: OK
        res2 = await client.get("/tools", headers=headers)
        assert res2.status_code == 200

        # Request 3: Exceeded -> 429 Too Many Requests
        res3 = await client.get("/tools", headers=headers)
        assert res3.status_code == 429
        assert "Retry-After" in res3.headers
        data = res3.json()
        assert data["error"]["code"] == "RATE_LIMIT_EXCEEDED"
