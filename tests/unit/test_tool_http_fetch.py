"""Unit and security tests for SSRF-protected HTTP fetch tool."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractContextManager, asynccontextmanager
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from agentkit.tools.builtin.http_fetch import (
    http_fetch,
    is_blocked_ip,
    validate_url_ssrf,
    verify_peer_address,
)
from agentkit.tools.models import ToolCall
from agentkit.tools.registry import ToolRegistry

PUBLIC_IP = "93.184.216.34"


class _FakeNetworkStream:
    """Minimal stand-in for httpcore's AnyIOStream exposing ``server_addr``."""

    def __init__(self, server_addr: tuple[str, int]) -> None:
        self._server_addr = server_addr

    def get_extra_info(self, key: str) -> tuple[str, int] | None:
        return self._server_addr if key == "server_addr" else None


def _patch_stream(response: httpx.Response) -> AbstractContextManager[Any]:
    """Patch ``AsyncClient.stream`` to yield ``response`` from an async context manager.

    ``http_fetch`` now streams the body rather than buffering it, so the tests drive
    ``AsyncClient.stream`` instead of ``AsyncClient.get``. A response built with ``text=`` or
    ``content=`` already streams correctly, so no body stubbing is needed.
    """

    @asynccontextmanager
    async def fake_stream(*_args: object, **_kwargs: object) -> AsyncIterator[httpx.Response]:
        yield response

    return patch.object(httpx.AsyncClient, "stream", fake_stream)


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
    with (
        patch(
            "agentkit.tools.builtin.http_fetch.resolve_host_ips",
            return_value=["10.0.0.1"],
        ),
        pytest.raises(ValueError, match="SSRF protection blocked"),
    ):
        validate_url_ssrf("https://malicious-internal-rebind.com")


@pytest.mark.asyncio
async def test_http_fetch_success() -> None:
    with patch(
        "agentkit.tools.builtin.http_fetch.resolve_host_ips",
        return_value=[PUBLIC_IP],
    ):
        mock_response = httpx.Response(
            status_code=200,
            text="<html>Hello World</html>",
            request=httpx.Request("GET", "https://example.com"),
        )

        with _patch_stream(mock_response):
            content = await http_fetch("https://example.com")
            assert "Hello World" in content


@pytest.mark.asyncio
async def test_http_fetch_blocks_private_redirect() -> None:
    with patch(
        "agentkit.tools.builtin.http_fetch.resolve_host_ips",
        side_effect=[
            [PUBLIC_IP],  # initial public url
            ["169.254.169.254"],  # redirect to metadata
        ],
    ):
        mock_redirect = httpx.Response(
            status_code=302,
            headers={"Location": "http://169.254.169.254/latest/meta-data/"},
            request=httpx.Request("GET", "https://example.com"),
        )
        with (
            _patch_stream(mock_redirect),
            pytest.raises(ValueError, match="SSRF protection blocked"),
        ):
            await http_fetch("https://example.com")


@pytest.mark.asyncio
async def test_http_fetch_size_limit() -> None:
    with patch(
        "agentkit.tools.builtin.http_fetch.resolve_host_ips",
        return_value=[PUBLIC_IP],
    ):
        large_body = "x" * 2000
        mock_response = httpx.Response(
            status_code=200,
            text=large_body,
            request=httpx.Request("GET", "https://example.com"),
        )

        with _patch_stream(mock_response):
            content = await http_fetch("https://example.com", max_chars=500)
            assert len(content) == 500


@pytest.mark.asyncio
async def test_http_fetch_rejects_rebind_to_unvalidated_peer() -> None:
    """A connection that lands off the validated addresses must be refused.

    Validation resolves the host to a public IP, but the client resolves again when it
    connects. This asserts the post-connect peer check closes that gap.
    """
    with patch(
        "agentkit.tools.builtin.http_fetch.resolve_host_ips",
        return_value=[PUBLIC_IP],
    ):
        mock_response = httpx.Response(
            status_code=200,
            text="internal secret",
            request=httpx.Request("GET", "https://rebind.example.com"),
        )
        mock_response.extensions["network_stream"] = _FakeNetworkStream(("127.0.0.1", 80))

        with (
            _patch_stream(mock_response),
            pytest.raises(ValueError, match="was not among the validated addresses"),
        ):
            await http_fetch("https://rebind.example.com")


def test_verify_peer_address_allows_validated_peer() -> None:
    response = httpx.Response(status_code=200, request=httpx.Request("GET", "https://example.com"))
    response.extensions["network_stream"] = _FakeNetworkStream((PUBLIC_IP, 443))

    verify_peer_address(response, [PUBLIC_IP])


def test_verify_peer_address_no_stream_is_permissive() -> None:
    """Without transport introspection the pre-flight validation remains the only guard."""
    response = httpx.Response(status_code=200, request=httpx.Request("GET", "https://example.com"))

    verify_peer_address(response, [PUBLIC_IP])


@pytest.mark.parametrize(
    ("ip", "blocked"),
    [
        ("127.0.0.1", True),
        ("169.254.169.254", True),
        ("10.1.2.3", True),
        ("not-an-ip", True),
        (PUBLIC_IP, False),
    ],
)
def test_is_blocked_ip(ip: str, blocked: bool) -> None:
    assert is_blocked_ip(ip) is blocked


@pytest.mark.asyncio
async def test_tool_registry_integration() -> None:
    registry = ToolRegistry()
    registry.register(http_fetch)

    with patch(
        "agentkit.tools.builtin.http_fetch.resolve_host_ips",
        return_value=[PUBLIC_IP],
    ):
        mock_response = httpx.Response(
            status_code=200,
            text="API Docs",
            request=httpx.Request("GET", "https://api.example.com"),
        )

        with _patch_stream(mock_response):
            call = ToolCall(
                id="c1", name="http_fetch", arguments={"url": "https://api.example.com"}
            )
            result = await registry.execute(call)
            assert result.ok is True
            assert result.output == "API Docs"

    bad_call = ToolCall(
        id="c2", name="http_fetch", arguments={"url": "http://169.254.169.254/latest"}
    )
    bad_result = await registry.execute(bad_call)
    assert bad_result.ok is False
    assert "SSRF protection blocked" in (bad_result.error or "")
