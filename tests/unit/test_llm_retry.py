from unittest.mock import AsyncMock, patch

import pytest

from agentkit.core.errors import (
    AuthenticationError,
    LLMProviderError,
    RateLimitExceededError,
)
from agentkit.llm.base import LLMResponse, Message
from agentkit.llm.fake import FakeLLMClient
from agentkit.llm.retry import (
    RetryingLLMClient,
    calculate_backoff_delay,
    is_transient_error,
    retry_with_backoff,
)


def test_calculate_backoff_delay() -> None:
    # Attempt 0
    d0 = calculate_backoff_delay(
        attempt=0, base_delay=1.0, backoff_factor=2.0, max_delay=10.0, jitter=0.5
    )
    assert 1.0 <= d0 <= 1.5

    # Attempt 1
    d1 = calculate_backoff_delay(
        attempt=1, base_delay=1.0, backoff_factor=2.0, max_delay=10.0, jitter=0.5
    )
    assert 2.0 <= d1 <= 2.5

    # Attempt 5 (exceeds max_delay 10.0)
    d5 = calculate_backoff_delay(
        attempt=5, base_delay=1.0, backoff_factor=2.0, max_delay=10.0, jitter=0.5
    )
    assert 10.0 <= d5 <= 10.5


def test_is_transient_error() -> None:
    # Transient errors: 429, 500, 502, 503, 504, TimeoutError, ConnectionError
    assert is_transient_error(RateLimitExceededError())
    assert is_transient_error(TimeoutError("Request timed out"))
    assert is_transient_error(ConnectionError("Connection reset"))
    assert is_transient_error(
        LLMProviderError(provider="test", message="server error", status_code=500)
    )
    assert is_transient_error(
        LLMProviderError(provider="test", message="unavailable", status_code=503)
    )
    assert is_transient_error(
        LLMProviderError(provider="test", message="rate limited", status_code=429)
    )

    # Non-transient errors: 400, 401, 403, 404, AuthenticationError, ValueError
    assert not is_transient_error(AuthenticationError())
    assert not is_transient_error(
        LLMProviderError(provider="test", message="bad request", status_code=400)
    )
    assert not is_transient_error(
        LLMProviderError(provider="test", message="forbidden", status_code=403)
    )
    assert not is_transient_error(
        LLMProviderError(provider="test", message="not found", status_code=404)
    )
    assert not is_transient_error(ValueError("Invalid argument"))


@pytest.mark.asyncio
async def test_retry_success_first_try() -> None:
    mock_func = AsyncMock(return_value="success")
    decorated = retry_with_backoff(max_retries=3, base_delay=0.01)(mock_func)

    res = await decorated("arg1")
    assert res == "success"
    assert mock_func.call_count == 1


@pytest.mark.asyncio
async def test_retry_transient_error_eventual_success() -> None:
    mock_func = AsyncMock(
        side_effect=[
            RateLimitExceededError(),
            LLMProviderError(provider="gemini", message="Service Unavailable", status_code=503),
            "recovered",
        ]
    )

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        decorated = retry_with_backoff(max_retries=3, base_delay=0.1, backoff_factor=2.0)(mock_func)
        res = await decorated()
        assert res == "recovered"
        assert mock_func.call_count == 3
        assert mock_sleep.call_count == 2


@pytest.mark.asyncio
async def test_retry_exhausted_raises_underlying() -> None:
    mock_func = AsyncMock(side_effect=TimeoutError("Request timed out"))

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        decorated = retry_with_backoff(max_retries=2, base_delay=0.05)(mock_func)
        with pytest.raises(TimeoutError, match="Request timed out"):
            await decorated()
        assert mock_func.call_count == 3  # initial + 2 retries
        assert mock_sleep.call_count == 2


@pytest.mark.asyncio
async def test_retry_non_transient_fails_immediately() -> None:
    mock_func = AsyncMock(
        side_effect=LLMProviderError(provider="openai", message="Bad Request", status_code=400)
    )

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        decorated = retry_with_backoff(max_retries=3, base_delay=0.05)(mock_func)
        with pytest.raises(LLMProviderError, match="Bad Request"):
            await decorated()
        assert mock_func.call_count == 1
        assert mock_sleep.call_count == 0


@pytest.mark.asyncio
async def test_retrying_llm_client_wrapper() -> None:
    fake_client = FakeLLMClient(
        responses=[
            LLMResponse(text="Hello world"),
        ]
    )
    retrying_client = RetryingLLMClient(fake_client, max_retries=2)

    messages = [Message.user("hi")]
    resp = await retrying_client.chat(messages=messages)
    assert resp.text == "Hello world"
