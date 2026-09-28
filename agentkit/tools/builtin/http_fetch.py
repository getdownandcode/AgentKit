"""SSRF-protected HTTP fetch tool blocking private, loopback, and metadata endpoints."""

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

from agentkit.tools.registry import tool

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_CHARS = 100_000
MAX_REDIRECTS = 3


def resolve_host_ips(hostname: str) -> list[str]:
    """Resolve a hostname to a list of IPv4/IPv6 address strings."""
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


def validate_url_ssrf(url: str) -> None:
    """Validate a URL against Server-Side Request Forgery (SSRF) guardrails.

    Args:
        url: The candidate URL to inspect.

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
    resolved_ips = resolve_host_ips(clean_host)
    if not resolved_ips:
        raise ValueError(f"No IP addresses could be resolved for host '{clean_host}'.")

    for ip_str in resolved_ips:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError as exc:
            raise ValueError(f"Invalid resolved IP '{ip_str}' for host '{clean_host}'.") from exc

        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError(
                f"SSRF protection blocked access to restricted IP address: {ip_str} for host '{clean_host}'."
            )


@tool
async def http_fetch(
    url: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Fetch the text content of a public web page via HTTP GET.

    Protected against SSRF: private IPs, localhost, and cloud metadata endpoints are blocked.
    Redirects are validated individually against SSRF restrictions.
    """
    current_url = url

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
        for _ in range(MAX_REDIRECTS + 1):
            validate_url_ssrf(current_url)

            response = await client.get(current_url, follow_redirects=False)

            if response.status_code in (301, 302, 303, 307, 308) and "Location" in response.headers:
                next_url = urljoin(current_url, response.headers["Location"])
                current_url = next_url
                continue

            response.raise_for_status()
            text = response.text
            if len(text) > max_chars:
                return text[:max_chars]
            return text

    raise ValueError(f"Exceeded maximum allowed redirects ({MAX_REDIRECTS}).")
