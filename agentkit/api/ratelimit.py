"""Redis-backed sliding window rate limiter for API key request volume."""

from __future__ import annotations

import logging
import math
import time
import uuid
from typing import cast

from fastapi import Depends
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from agentkit.api.auth import get_current_settings, verify_api_key
from agentkit.api.deps import get_redis_client
from agentkit.config import Settings
from agentkit.core.errors import RateLimitExceededError

logger = logging.getLogger(__name__)

#: Sliding-window admission script, executed atomically inside Redis.
#:
#: The trim, the cardinality check and the insert have to happen as one indivisible
#: step. Performing them as separate round trips leaves a window between reading the
#: count and writing the new member in which concurrent requests all observe the same
#: pre-insert cardinality and are admitted together, which lets a client exceed its
#: quota simply by sending requests in parallel.
#:
#: KEYS: 1 - the sorted set tracking this client's window.
#: ARGV: 1 - current unix timestamp, 2 - window length in seconds, 3 - request limit,
#:        4 - unique member for this request, 5 - key TTL in seconds.
#: Returns: {admitted (1/0), retry_after_seconds}.
SLIDING_WINDOW_SCRIPT = """
local key       = KEYS[1]
local now       = tonumber(ARGV[1])
local window    = tonumber(ARGV[2])
local limit     = tonumber(ARGV[3])
local member    = ARGV[4]
local key_ttl   = tonumber(ARGV[5])

redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)

if count >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_after = key_ttl
    if oldest[2] then
        retry_after = math.ceil(tonumber(oldest[2]) + window - now)
        if retry_after < 1 then retry_after = 1 end
    end
    return {0, retry_after}
end

redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, key_ttl)
return {1, 0}
"""


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
            member = f"{now}:{uuid.uuid4().hex[:8]}"

            admitted, retry_after = await self._check_window(
                redis_client=redis_client,
                redis_key=redis_key,
                now=now,
                window=effective_window,
                limit=effective_limit,
                member=member,
            )

            if not admitted:
                logger.warning(
                    "Rate limit exceeded for key %s (limit: %d). Retry after: %ds",
                    key[:8] + "...",
                    effective_limit,
                    retry_after,
                )
                raise RateLimitExceededError(retry_after=retry_after)
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

    async def _check_window(
        self,
        redis_client: Redis,
        redis_key: str,
        now: float,
        window: float,
        limit: int,
        member: str,
    ) -> tuple[bool, int]:
        """Run the sliding-window script and return (admitted, retry_after).

        Falls back to a non-atomic equivalent if the Redis deployment does not
        permit scripting, since refusing all traffic would be worse than a
        best-effort limit.
        """
        key_ttl = int(window) + 5
        try:
            # redis-py types eval() as `Any | Awaitable[Any]`, which mypy rejects in a
            # typed context; the cast keeps the declared return type honest.
            result = await redis_client.eval(  # type: ignore[no-untyped-call]
                SLIDING_WINDOW_SCRIPT,
                1,
                redis_key,
                now,
                window,
                limit,
                member,
                key_ttl,
            )
        except ResponseError as exc:
            logger.warning(
                "Redis scripting unavailable (%s); falling back to non-atomic rate limiting.",
                exc,
            )
            return await self._check_window_unatomic(
                redis_client, redis_key, now, window, limit, member, key_ttl
            )

        admitted, retry_after = cast("tuple[int, int]", tuple(result))
        return bool(admitted), int(retry_after)

    async def _check_window_unatomic(
        self,
        redis_client: Redis,
        redis_key: str,
        now: float,
        window: float,
        limit: int,
        member: str,
        key_ttl: int,
    ) -> tuple[bool, int]:
        """Best-effort sliding-window check for servers without scripting support."""
        pipe = redis_client.pipeline(transaction=True)
        pipe.zremrangebyscore(redis_key, "-inf", now - window)
        pipe.zcard(redis_key)
        results = await pipe.execute()
        if int(results[1]) >= limit:
            oldest = await redis_client.zrange(redis_key, 0, 0, withscores=True)
            retry_after = (
                max(1, math.ceil(float(oldest[0][1]) + window - now)) if oldest else int(window)
            )
            return False, retry_after

        pipe = redis_client.pipeline(transaction=True)
        pipe.zadd(redis_key, {member: now})
        pipe.expire(redis_key, key_ttl)
        await pipe.execute()
        return True, 0

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
