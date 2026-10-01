"""Exponential backoff retry decorator and wrapper with jitter for transient LLM errors."""

import asyncio
import logging
import random
from collections.abc import Callable, Coroutine
from functools import wraps
from typing import Any, TypeVar

from agentkit.core.errors import (
    AuthenticationError,
    LLMProviderError,
    RateLimitExceededError,
)
from agentkit.llm.base import LLMClient, LLMResponse, Message
from agentkit.tools.models import ToolSchema

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Coroutine[Any, Any, Any]])


def is_transient_error(exc: Exception) -> bool:
    """Determine whether an error is transient and eligible for retry.

    Transient errors include:
    - RateLimitExceededError / 429
    - Server-side errors (500, 502, 503, 504)
    - Network/connection errors and timeouts

    Non-transient errors (never retried) include:
    - 400 Bad Request
    - 401 Unauthorized / AuthenticationError
    - 403 Forbidden
    - 404 Not Found
    - Invalid client parameters / ValueError
    """
    if isinstance(exc, AuthenticationError):
        return False

    if isinstance(exc, RateLimitExceededError):
        return True

    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    if isinstance(exc, LLMProviderError):
        if exc.status_code is not None:
            if exc.status_code in (429, 500, 502, 503, 504):
                return True
            if 400 <= exc.status_code < 500:
                return False

        err_msg = exc.message.lower()
        transient_phrases = (
            "rate limit",
            "quota",
            "overloaded",
            "resource exhausted",
            "unavailable",
            "timeout",
            "timed out",
            "connection error",
            "503",
            "500",
            "502",
            "504",
        )
        return any(phrase in err_msg for phrase in transient_phrases)

    return False


def calculate_backoff_delay(
    attempt: int,
    base_delay: float = 0.5,
    backoff_factor: float = 2.0,
    max_delay: float = 10.0,
    jitter: float = 0.1,
) -> float:
    """Calculate exponential backoff delay with added random jitter.

    Formula: min(max_delay, base_delay * (backoff_factor ** attempt)) + random.uniform(0, jitter)
    """
    exponential = base_delay * (backoff_factor**attempt)
    capped = min(max_delay, exponential)
    jitter_val = random.uniform(0, jitter) if jitter > 0 else 0.0
    return capped + jitter_val


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 0.5,
    backoff_factor: float = 2.0,
    max_delay: float = 10.0,
    jitter: float = 0.1,
) -> Callable[[F], F]:
    """Decorator for async functions that retries on transient errors with exponential backoff and jitter."""

    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            attempt = 0
            while True:
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    if attempt >= max_retries or not is_transient_error(exc):
                        raise
                    delay = calculate_backoff_delay(
                        attempt=attempt,
                        base_delay=base_delay,
                        backoff_factor=backoff_factor,
                        max_delay=max_delay,
                        jitter=jitter,
                    )
                    logger.warning(
                        "Transient error encountered in %s (attempt %d/%d): %s. Retrying in %.2fs...",
                        func.__name__,
                        attempt + 1,
                        max_retries,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    attempt += 1

        return wrapper  # type: ignore[return-value]

    return decorator


class RetryingLLMClient(LLMClient):
    """Decorator/wrapper around an LLMClient that adds automatic retry logic with backoff."""

    def __init__(
        self,
        client: LLMClient,
        max_retries: int = 3,
        base_delay: float = 0.5,
        backoff_factor: float = 2.0,
        max_delay: float = 10.0,
        jitter: float = 0.1,
    ) -> None:
        self._client = client
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._backoff_factor = backoff_factor
        self._max_delay = max_delay
        self._jitter = jitter

        # Decorate the chat method with configured backoff parameters
        self._retrying_chat = retry_with_backoff(
            max_retries=self._max_retries,
            base_delay=self._base_delay,
            backoff_factor=self._backoff_factor,
            max_delay=self._max_delay,
            jitter=self._jitter,
        )(self._client.chat)

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
    ) -> LLMResponse:
        """Execute chat turn with retries on transient errors."""
        return await self._retrying_chat(messages=messages, tools=tools)

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute lookups to the underlying client.

        ``_client`` is resolved via ``__dict__`` rather than ``getattr`` on purpose: it is
        absent when ``__init__`` raises part-way, and ``getattr(self, "_client")`` would
        re-enter this method and recurse until the stack blew up.
        """
        if name == "_client":
            raise AttributeError(
                f"{type(self).__name__!r} object has no attribute '_client'; "
                "the wrapped client was never assigned."
            )
        client = self.__dict__.get("_client")
        if client is None:
            raise AttributeError(
                f"{type(self).__name__!r} object has no attribute {name!r}; "
                "the wrapped client was never assigned."
            )
        return getattr(client, name)
