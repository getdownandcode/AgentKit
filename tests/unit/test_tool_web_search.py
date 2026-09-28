"""Unit tests for web search tool adapter."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from agentkit.tools.builtin.web_search import search_web, web_search
from agentkit.tools.models import ToolCall
from agentkit.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_missing_api_key_raises_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentkit.config import get_settings

    monkeypatch.setattr(get_settings(), "SEARCH_API_KEY", None)

    with pytest.raises(ValueError, match="SEARCH_API_KEY is not configured"):
        await search_web("python documentation")


@pytest.mark.asyncio
async def test_empty_query_raises_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentkit.config import get_settings

    monkeypatch.setattr(get_settings(), "SEARCH_API_KEY", "test_key")

    with pytest.raises(ValueError, match="Empty or whitespace-only search query"):
        await search_web("   ")


@pytest.mark.asyncio
async def test_successful_search(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentkit.config import get_settings

    monkeypatch.setattr(get_settings(), "SEARCH_API_KEY", "mock_key")

    mock_json = {
        "results": [
            {
                "title": "FastAPI Framework",
                "url": "https://fastapi.tiangolo.com",
                "content": "FastAPI is a modern, fast web framework for building APIs with Python.",
            },
            {
                "title": "Pydantic Documentation",
                "url": "https://docs.pydantic.dev",
                "content": "Data validation using Python type annotations.",
            },
        ]
    }

    mock_resp = httpx.Response(
        status_code=200,
        json=mock_json,
        request=httpx.Request("POST", "https://api.tavily.com/search"),
    )

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        output = await search_web("fastapi vs pydantic")

        assert "1. FastAPI Framework" in output
        assert "https://fastapi.tiangolo.com" in output
        assert "2. Pydantic Documentation" in output
        assert "Data validation using Python" in output


@pytest.mark.asyncio
async def test_empty_results_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentkit.config import get_settings

    monkeypatch.setattr(get_settings(), "SEARCH_API_KEY", "mock_key")

    mock_resp = httpx.Response(
        status_code=200,
        json={"results": []},
        request=httpx.Request("POST", "https://api.tavily.com/search"),
    )

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        output = await search_web("gibberish nonexistent query 12345")
        assert "No search results found" in output


@pytest.mark.asyncio
async def test_web_search_registry_integration(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentkit.config import get_settings

    monkeypatch.setattr(get_settings(), "SEARCH_API_KEY", "mock_key")

    mock_json = {
        "results": [
            {
                "title": "Python 3.12 Release Notes",
                "url": "https://docs.python.org/3.12/",
                "content": "Python 3.12 introduces new features and optimizations.",
            }
        ]
    }

    mock_resp = httpx.Response(
        status_code=200,
        json=mock_json,
        request=httpx.Request("POST", "https://api.tavily.com/search"),
    )

    registry = ToolRegistry()
    registry.register(web_search)

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        call = ToolCall(id="c1", name="web_search", arguments={"query": "python 3.12"})
        result = await registry.execute(call)
        assert result.ok is True
        assert "Python 3.12 Release Notes" in result.output

    # Missing API key failure via registry
    monkeypatch.setattr(get_settings(), "SEARCH_API_KEY", None)
    bad_call = ToolCall(id="c2", name="web_search", arguments={"query": "test"})
    bad_result = await registry.execute(bad_call)
    assert bad_result.ok is False
    assert "SEARCH_API_KEY is not configured" in (bad_result.error or "")
