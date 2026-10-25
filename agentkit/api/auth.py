"""API key authentication dependency validating X-API-Key header."""

import hmac
import logging

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader

from agentkit.config import Settings, get_settings
from agentkit.core.errors import AuthenticationError

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_current_settings(request: Request) -> Settings:
    """Extract settings from app.state or fallback to get_settings()."""
    app_settings: Settings | None = getattr(request.app.state, "settings", None)
    return app_settings or get_settings()


async def verify_api_key(
    api_key: str | None = Security(api_key_header),
    settings: Settings = Depends(get_current_settings),
) -> str:
    """Validate client API key from X-API-Key header against configured authorized keys.

    The presented key is normalised the same way configured keys are, so surrounding
    whitespace does not change the outcome. Comparison goes through
    :func:`hmac.compare_digest` for every configured key without short-circuiting: a plain
    ``in`` check on a list is a timing oracle that leaks how much of a key matched.

    Returns:
        The normalised key, which is what rate limiting and logs use.

    Raises:
        AuthenticationError: If the key is missing or matches no configured key.
    """
    presented = (api_key or "").strip()
    matched = False
    if presented:
        presented_bytes = presented.encode("utf-8")
        for expected in settings.api_keys_list:
            # Deliberately not short-circuiting: comparing every key keeps the work (and so
            # the timing) independent of which key matched.
            matched |= hmac.compare_digest(presented_bytes, expected.encode("utf-8"))

    if not matched:
        logger.warning("Unauthorized API request with missing or invalid key.")
        raise AuthenticationError("Invalid or missing API key.")
    return presented
