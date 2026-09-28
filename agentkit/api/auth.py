"""API key authentication dependency validating X-API-Key header."""

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
    """Validate client API key from X-API-Key header against configured authorized keys."""
    if not api_key or api_key not in settings.api_keys_list:
        logger.warning("Unauthorized API request with missing or invalid key.")
        raise AuthenticationError("Invalid or missing API key.")
    return api_key
