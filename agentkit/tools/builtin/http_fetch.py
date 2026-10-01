"""SSRF-protected HTTP fetch tool blocking private, loopback, and metadata endpoints."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlparse

import httpx

from agentkit.tools.registry import tool

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_CHARS = 100_000
MAX_REDIRECTS = 3

#: Hard ceiling on bytes read from a response body. ``max_chars`` bounds what is *returned*;
#: this bounds what is *buffered*, so an oversized or malicious payload cannot exhaust memory
#: before truncation has a chance to apply.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def resolve_host_ips(hostname: str) -> list[str]:
    """Resolve a hostname to a list of IPv4/IPv6 address strings.

    Blocking by design (``socket.getaddrinfo`` performs synchronous network I/O); async callers
    must go through :func:`resolve_host_ips_async` so the event loop is not stalled.
    """
    try:
        # Check if already a valid literal IP
        ipaddress.ip_address(hostname)
        return [hostname]
    except ValueError:
        pass

    try:
        addr_info = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        return list({str(info[4][0]) for info in addr_info})
    except socket.gaierror as exc:
        raise ValueError(f"Failed to resolve host '{hostname}': {exc}") from exc


async def resolve_host_ips_async(hostname: str) -> list[str]:
    """Resolve a hostname off the event loop.

    DNS resolution is blocking network I/O; running it inline would stall every concurrent
    request for the duration of the resolver timeout.
    """
    return await asyncio.to_thread(resolve_host_ips, hostname)


def is_blocked_ip(ip_str: str) -> bool:
    """Return True when an IP address must not be reachable through this tool."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        # An unparseable address cannot be vetted, so treat it as blocked.
        return True

    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_url_ssrf(url: str, resolved_ips: list[str] | None = None) -> list[str]:
    """Validate a URL against Server-Side Request Forgery (SSRF) guardrails.

    Args:
        url: The candidate URL to inspect.
        resolved_ips: Optional pre-resolved addresses, letting a caller resolve once and reuse
            the result instead of paying for a second lookup.

    Returns:
        The list of validated public IP addresses the host resolves to.

    Raises:
        ValueError: If scheme is non-HTTP/HTTPS, or host resolves to loopback,
            RFC1918 private subnets, link-local addresses, or cloud metadata endpoints.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"Disallowed URL scheme: '{scheme}'. Only http and https are allowed.")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL must contain a valid hostname.")

    clean_host = hostname.strip().lower()
    if clean_host in ("localhost", "localhost.localdomain"):
        raise ValueError(f"SSRF protection blocked access to restricted host '{clean_host}'.")

    # Resolve all candidate IPs (both IPv4 and IPv6)
    ips = resolved_ips if resolved_ips is not None else resolve_host_ips(clean_host)
    if not ips:
        raise ValueError(f"No IP addresses could be resolved for host '{clean_host}'.")

    for ip_str in ips:
        if is_blocked_ip(ip_str):
            raise ValueError(
                f"SSRF protection blocked access to restricted IP address: {ip_str} for host '{clean_host}'."
            )

    return ips


async def validate_url_ssrf_async(url: str) -> list[str]:
    """Async SSRF validation returning the resolved, vetted public IPs for the host."""
    parsed = urlparse(url)
    hostname = parsed.hostname
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"Disallowed URL scheme: '{scheme}'. Only http and https are allowed.")
    if not hostname:
        raise ValueError("URL must contain a valid hostname.")

    ips = await resolve_host_ips_async(hostname.strip().lower())
    return validate_url_ssrf(url, resolved_ips=ips)


def verify_peer_address(response: httpx.Response, allowed_ips: list[str]) -> None:
    """Confirm the socket actually connected to an IP that passed validation.

    Validating a hostname and then letting the HTTP client resolve it again leaves a
    time-of-check/time-of-use gap: a short-TTL DNS answer can hand back a public address
    during validation and a loopback or metadata address during connection. Checking the real
    peer address of the established socket closes that gap.

    Raises:
        ValueError: If the connection landed on an address outside ``allowed_ips``.
    """
    stream = response.extensions.get("network_stream")
    if stream is None:
        # No transport introspection available (some transports and test doubles); the
        # pre-flight validation above is the only guard in that case.
        logger.debug("No network stream available to verify peer address; skipping peer check.")
        return

    server_addr = stream.get_extra_info("server_addr")
    if not server_addr:
        return

    peer_ip = server_addr[0]
    normalized_allowed = {str(ipaddress.ip_address(ip)) for ip in allowed_ips}
    normalized_peer = str(ipaddress.ip_address(peer_ip))
    if normalized_peer not in normalized_allowed:
        raise ValueError(
            f"SSRF protection blocked connection to '{peer_ip}': it was not among the validated "
            f"addresses for the requested host ({', '.join(sorted(normalized_allowed))})."
        )


@tool
async def http_fetch(
    url: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Fetch the text content of a public web page via HTTP GET.

    Protected against SSRF: private IPs, localhost, and cloud metadata endpoints are blocked.
    Redirects are validated individually against SSRF restrictions, and the connected peer
    address is re-checked against the validated addresses.
    """
    current_url = url
    read_budget = min(MAX_RESPONSE_BYTES, max(max_chars, 1))

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
        for _ in range(MAX_REDIRECTS + 1):
            allowed_ips = await validate_url_ssrf_async(current_url)

            async with client.stream("GET", current_url, follow_redirects=False) as response:
                verify_peer_address(response, allowed_ips)

                if (
                    response.status_code in (301, 302, 303, 307, 308)
                    and "Location" in response.headers
                ):
                    next_url = urljoin(current_url, response.headers["Location"])
                    current_url = next_url
                    continue

                response.raise_for_status()

                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    remaining = read_budget - total
                    if remaining <= 0:
                        break
                    if len(chunk) > remaining:
                        chunk = chunk[:remaining]
                    chunks.append(chunk)
                    total += len(chunk)

                text = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
                return text[:max_chars]

    raise ValueError(f"Exceeded maximum allowed redirects ({MAX_REDIRECTS}).")
