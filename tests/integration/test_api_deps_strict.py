"""Integration tests for strict infrastructure resolution in FastAPI dependencies."""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentkit.api.deps import (
    get_llm_client,
    get_memory_store,
    get_trace_sink,
)
from agentkit.api.main import create_app
from agentkit.config import Settings
from agentkit.core.errors import AuthenticationError, ServiceUnavailableError
from agentkit.llm.fake import FakeLLMClient


class MockRequest:
    """Minimal mock request with customizable app state."""

    def __init__(self, app_state: dict[str, Any] | None = None) -> None:
        class State:
            pass

        self.app = type("App", (), {"state": State()})()
        if app_state:
            for k, v in app_state.items():
                setattr(self.app.state, k, v)


def test_memory_store_raises_when_database_factory_absent() -> None:
    """get_memory_store must raise ServiceUnavailableError for database if session_factory is missing."""
    req = MockRequest()
    with pytest.raises(ServiceUnavailableError) as exc_info:
        get_memory_store(req)  # type: ignore[arg-type]
    assert exc_info.value.service == "database"


def test_memory_store_raises_when_redis_client_absent() -> None:
    """get_memory_store must raise ServiceUnavailableError for redis if redis_client is missing."""
    dummy_factory = object()
    req = MockRequest(app_state={"session_factory": dummy_factory})
    with pytest.raises(ServiceUnavailableError) as exc_info:
        get_memory_store(req)  # type: ignore[arg-type]
    assert exc_info.value.service == "redis"


def test_trace_sink_raises_when_database_factory_absent() -> None:
    """get_trace_sink must raise ServiceUnavailableError if session_factory is missing."""
    req = MockRequest()
    with pytest.raises(ServiceUnavailableError) as exc_info:
        get_trace_sink(req)  # type: ignore[arg-type]
    assert exc_info.value.service == "database"


def test_llm_client_does_not_silently_fallback_to_fake() -> None:
    """get_llm_client must raise error if live provider fails rather than returning FakeLLMClient."""
    settings = Settings(
        API_KEYS="secret_token",
        LLM_PROVIDER="gemini",
        GEMINI_API_KEY="",
    )
    req = MockRequest()
    # If GEMINI_API_KEY is empty/invalid and ADC fails, it must raise AuthenticationError, not return FakeLLMClient
    try:
        client = get_llm_client(req, settings=settings)  # type: ignore[arg-type]
        assert not isinstance(client, FakeLLMClient), (
            "get_llm_client must not return FakeLLMClient when LLM_PROVIDER is 'gemini'"
        )
    except AuthenticationError:
        pass  # Expected authentic error, not swallowed into FakeLLMClient


def test_llm_client_returns_fake_when_explicitly_configured() -> None:
    """get_llm_client returns FakeLLMClient only when LLM_PROVIDER='fake' is explicitly chosen."""
    settings = Settings(
        API_KEYS="secret_token",
        LLM_PROVIDER="fake",
    )
    req = MockRequest()
    client = get_llm_client(req, settings=settings)  # type: ignore[arg-type]
    assert isinstance(client, FakeLLMClient)


def test_http_endpoint_returns_503_when_infrastructure_missing() -> None:
    """HTTP requests against endpoints requiring memory or trace return 503 if services are missing."""
    settings = Settings(API_KEYS="secret_token")
    app = create_app(settings=settings)
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"X-API-Key": "secret_token"}

    # GET /runs/{run_id} needs get_memory_store -> should fail with 503 SERVICE_UNAVAILABLE
    resp = client.get(f"/runs/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 503
    data = resp.json()
    assert data["error"]["code"] == "SERVICE_UNAVAILABLE"
