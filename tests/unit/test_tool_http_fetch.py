"""Unit and security tests for SSRF-protected HTTP fetch tool."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from agentkit.tools.builtin.http_fetch import http_fetch, validate_url_ssrf
from agentkit.tools.models import ToolCall
from agentkit.tools.registry import ToolRegistry


@pytest.mark.parametrize(
    "invalid_scheme",
    [
        "file:///etc/passwd",
        "ftp://ftp.example.com/file",
        "gopher://example.com",
        "javascript:alert(1)",
        "mailto:test@example.com",
        "data:text/plain;base64,SGVsbG8=",
    ],
)
def test_disallowed_schemes_rejected(invalid_scheme: str) -> None:
    with pytest.raises(ValueError, match="Disallowed URL scheme"):
        validate_url_ssrf(invalid_scheme)


@pytest.mark.parametrize(
    "blocked_url",
    [
        "http://localhost/admin",
        "http://127.0.0.1:8080/metrics",
        "http://127.1.2.3/",
        "http://[::1]/secret",
        "http://0.0.0.0:8000/",
        "http://10.0.0.5/api",
        "http://172.16.50.1/",
        "http://192.168.1.254/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[fe80::1]/",
    ],
)
def test_private_and_metadata_ips_blocked(blocked_url: str) -> None:
    with pytest.raises(ValueError, match="SSRF protection blocked"):
        validate_url_ssrf(blocked_url)


def test_public_url_validation_passes() -> None:
    # example.com resolves to public IP
    with patch(
        "agentkit.tools.builtin.http_fetch.resolve_host_ips",
        return_value=["93.184.216.34"],
    ):
        validate_url_ssrf("https://example.com/about")


def test_dns_rebinding_or_private_resolution_blocked() -> None:
    # If a public domain resolves to a private IP
    with patch(
        "agentkit.tools.builtin.http_fetch.resolve_host_ips",
        return_value=["10.0.0.1"],
    ):
        with pytest.raises(ValueError, match="SSRF protection blocked"):
            validate_url_ssrf("https://malicious-internal-rebind.com")


@pytest.mark.asyncio
async def test_http_fetch_success() -> None:
    with patch(
        "agentkit.tools.builtin.http_fetch.resolve_host_ips",
        return_value=["93.184.216.34"],
    ):
        mock_response = httpx.Response(
            status_code=200,
            text="<html>Hello World</html>",
            request=httpx.Request("GET", "https://example.com"),
        )
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            content = await http_fetch("https://example.com")
            assert "Hello World" in content


@pytest.mark.asyncio
async def test_http_fetch_blocks_private_redirect() -> None:
    with patch(
        "agentkit.tools.builtin.http_fetch.resolve_host_ips",
        side_effect=[
            ["93.184.216.34"],  # initial public url
            ["169.254.169.254"],  # redirect to metadata
        ],
    ):
        mock_redirect = httpx.Response(
            status_code=302,
            headers={"Location": "http://169.254.169.254/latest/meta-data/"},
            request=httpx.Request("GET", "https://example.com"),
        )
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_redirect

            with pytest.raises(ValueError, match="SSRF protection blocked"):
                await http_fetch("https://example.com")


@pytest.mark.asyncio
async def test_http_fetch_size_limit() -> None:
    with patch(
        "agentkit.tools.builtin.http_fetch.resolve_host_ips",
        return_value=["93.184.216.34"],
    ):
        large_body = "x" * 2000
        mock_response = httpx.Response(
            status_code=200,
            text=large_body,
            request=httpx.Request("GET", "https://example.com"),
        )
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            content = await http_fetch("https://example.com", max_chars=500)
            assert len(content) == 500


@pytest.mark.asyncio
async def test_tool_registry_integration() -> None:
    registry = ToolRegistry()
    registry.register(http_fetch)

    with patch(
        "agentkit.tools.builtin.http_fetch.resolve_host_ips",
        return_value=["93.184.216.34"],
    ):
        mock_response = httpx.Response(
            status_code=200,
            text="API Docs",
            request=httpx.Request("GET", "https://api.example.com"),
        )
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            call = ToolCall(id="c1", name="http_fetch", arguments={"url": "https://api.example.com"})
            result = await registry.execute(call)
            assert result.ok is True
            assert result.output == "API Docs"

    bad_call = ToolCall(id="c2", name="http_fetch", arguments={"url": "http://169.254.169.254/latest"})
    bad_result = await registry.execute(bad_call)
    assert bad_result.ok is False
    assert "SSRF protection blocked" in (bad_result.error or "")
