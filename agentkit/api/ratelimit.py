"""Redis-backed sliding window rate limiter for API key request volume."""

from __future__ import annotations

import logging
import math
import time
import uuid

from fastapi import Depends
from redis.asyncio import Redis

from agentkit.api.auth import get_current_settings, verify_api_key
from agentkit.api.deps import get_redis_client
from agentkit.config import Settings
from agentkit.core.errors import RateLimitExceededError

logger = logging.getLogger(__name__)


class RateLimiter:
    """Sliding window rate limiter enforcing request volume limits per client key.

    Uses Redis Sorted Sets (ZSET) for distributed, atomic sliding-window rate tracking.
    Falls back gracefully to an in-memory sliding window when Redis is unavailable.
    """

    def __init__(
        self,
        requests_per_window: int | None = None,
        window_seconds: int = 60,
        prefix: str = "ratelimit:",
    ) -> None:
        self.requests_per_window = requests_per_window
        self.window_seconds = window_seconds
        self.prefix = prefix
        self._memory_store: dict[str, list[float]] = {}

    async def check(
        self,
        key: str,
        redis_client: Redis | None = None,
        limit: int | None = None,
        window_seconds: int | None = None,
    ) -> None:
        """Validate whether the key has exceeded its rate limit in the current window."""
        now = time.time()
        effective_limit = limit or self.requests_per_window or 30
        effective_window = float(window_seconds or self.window_seconds)

        if redis_client is not None:
            redis_key = f"{self.prefix}{key}"
            clear_before = now - effective_window

            pipe = redis_client.pipeline(transaction=True)
            pipe.zremrangebyscore(redis_key, "-inf", clear_before)
            pipe.zcard(redis_key)
            results = await pipe.execute()
            current_count = int(results[1])

            if current_count >= effective_limit:
                oldest_entries = await redis_client.zrange(redis_key, 0, 0, withscores=True)
                if oldest_entries:
                    oldest_score = float(oldest_entries[0][1])
                    retry_after = max(1, math.ceil(oldest_score + effective_window - now))
                else:
                    retry_after = int(effective_window)

                logger.warning(
                    "Rate limit exceeded for key %s (count: %d, limit: %d). Retry after: %ds",
                    key[:8] + "...",
                    current_count,
                    effective_limit,
                    retry_after,
                )
                raise RateLimitExceededError(retry_after=retry_after)

            member = f"{now}:{uuid.uuid4().hex[:8]}"
            pipe = redis_client.pipeline(transaction=True)
            pipe.zadd(redis_key, {member: now})
            pipe.expire(redis_key, int(effective_window) + 5)
            await pipe.execute()
        else:
            # In-memory sliding window fallback
            active_timestamps = [
                t for t in self._memory_store.get(key, []) if t > now - effective_window
            ]

            if len(active_timestamps) >= effective_limit:
                oldest_score = active_timestamps[0]
                retry_after = max(1, math.ceil(oldest_score + effective_window - now))
                self._memory_store[key] = active_timestamps
                logger.warning(
                    "In-memory rate limit exceeded for key %s. Retry after: %ds",
                    key[:8] + "...",
                    retry_after,
                )
                raise RateLimitExceededError(retry_after=retry_after)

            active_timestamps.append(now)
            self._memory_store[key] = active_timestamps

    async def __call__(
        self,
        api_key: str = Depends(verify_api_key),
        redis_client: Redis | None = Depends(get_redis_client),
        settings: Settings = Depends(get_current_settings),
    ) -> None:
        """FastAPI dependency to rate-limit endpoints."""
        limit = (
            self.requests_per_window
            if self.requests_per_window is not None
            else settings.RATE_LIMIT_PER_MIN
        )
        await self.check(
            key=api_key,
            redis_client=redis_client,
            limit=limit,
            window_seconds=self.window_seconds,
        )


rate_limit = RateLimiter()
