"""Factory functions for creating LLMClient instances based on configuration."""

import logging
from typing import Any

from agentkit.config import Settings
from agentkit.llm.base import LLMClient
from agentkit.llm.fake import FakeLLMClient
from agentkit.llm.gemini import GeminiClient
from agentkit.llm.openai import OpenAIClient
from agentkit.llm.retry import RetryingLLMClient

logger = logging.getLogger(__name__)

SUPPORTED_PROVIDERS = ("gemini", "openai", "fake")


def create_llm_client(
    provider: str,
    api_key: str | None = None,
    model: str | None = None,
    **kwargs: Any,
) -> LLMClient:
    """Instantiate an LLMClient for the specified provider.

    Args:
        provider: Provider identifier ('gemini', 'openai', 'fake').
        api_key: Optional provider API key.
        model: Optional model name.
        **kwargs: Additional provider-specific constructor options.

    Returns:
        Configured LLMClient instance.

    Raises:
        ValueError: If provider is unsupported.
    """
    normalized_provider = provider.strip().lower()

    if normalized_provider == "gemini":
        default_model = model or "gemini-3.1-flash-lite"
        return GeminiClient(api_key=api_key, model=default_model, **kwargs)

    if normalized_provider == "openai":
        default_model = model or "gpt-4o"
        return OpenAIClient(api_key=api_key, model=default_model, **kwargs)

    if normalized_provider == "fake":
        return FakeLLMClient(**kwargs)

    raise ValueError(
        f"Unsupported LLM provider: '{provider}'. Supported providers are: {', '.join(SUPPORTED_PROVIDERS)}"
    )


def create_llm_client_from_settings(settings: Settings) -> LLMClient:
    """Instantiate an LLMClient using settings from environment or Settings object.

    Args:
        settings: Application Settings instance.

    Returns:
        Configured LLMClient instance (wrapped with RetryingLLMClient for live providers).
    """
    provider = settings.LLM_PROVIDER.strip().lower()
    api_key: str | None = None

    if provider == "gemini":
        api_key = settings.GEMINI_API_KEY
    elif provider == "openai":
        api_key = settings.OPENAI_API_KEY

    client = create_llm_client(
        provider=provider,
        api_key=api_key,
        model=settings.LLM_MODEL,
    )

    if provider in ("gemini", "openai") and settings.MAX_RETRIES > 0:
        return RetryingLLMClient(
            client=client,
            max_retries=settings.MAX_RETRIES,
        )

    return client
