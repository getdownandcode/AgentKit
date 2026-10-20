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

    Normalises the presented key (stripping whitespace) and uses constant-time comparison
    (`hmac.compare_digest`) across all configured keys to eliminate timing oracles.
    """
    if not api_key:
        logger.warning("Unauthorized API request with missing or invalid key.")
        raise AuthenticationError("Invalid or missing API key.")

    normalized_key = api_key.strip()
    if not normalized_key:
        logger.warning("Unauthorized API request with missing or invalid key.")
        raise AuthenticationError("Invalid or missing API key.")

    presented_bytes = normalized_key.encode("utf-8")
    matched = False
    for valid_key in settings.api_keys_list:
        if hmac.compare_digest(presented_bytes, valid_key.encode("utf-8")):
            matched = True

    if not matched:
        logger.warning("Unauthorized API request with missing or invalid key.")
        raise AuthenticationError("Invalid or missing API key.")

    return normalized_key
