import pytest

from agentkit.config import Settings
from agentkit.llm.factory import create_llm_client, create_llm_client_from_settings
from agentkit.llm.fake import FakeLLMClient
from agentkit.llm.gemini import GeminiClient
from agentkit.llm.openai import OpenAIClient


def test_create_fake_client() -> None:
    client = create_llm_client(provider="fake")
    assert isinstance(client, FakeLLMClient)

    client_upper = create_llm_client(provider="FAKE")
    assert isinstance(client_upper, FakeLLMClient)


def test_create_gemini_client() -> None:
    client = create_llm_client(
        provider="gemini",
        api_key="test-key-gemini",
        model="gemini-2.5-pro",
    )
    assert isinstance(client, GeminiClient)
    assert client.model == "gemini-2.5-pro"
    assert client.api_key == "test-key-gemini"


def test_create_openai_client() -> None:
    client = create_llm_client(
        provider="openai",
        api_key="sk-test-key-openai",
        model="gpt-4o-mini",
    )
    assert isinstance(client, OpenAIClient)
    assert client.model == "gpt-4o-mini"
    assert client.api_key == "sk-test-key-openai"


def test_create_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        create_llm_client(provider="claude")


def test_create_from_settings_gemini() -> None:
    settings = Settings(
        LLM_PROVIDER="gemini",
        LLM_MODEL="gemini-2.0-flash",
        GEMINI_API_KEY="test-gemini-key",
    )
    client = create_llm_client_from_settings(settings)
    assert isinstance(client, GeminiClient)
    assert client.model == "gemini-2.0-flash"


def test_create_from_settings_openai() -> None:
    settings = Settings(
        LLM_PROVIDER="openai",
        LLM_MODEL="gpt-4o",
        OPENAI_API_KEY="test-openai-key",
    )
    client = create_llm_client_from_settings(settings)
    assert isinstance(client, OpenAIClient)
    assert client.model == "gpt-4o"


def test_create_from_settings_fake() -> None:
    settings = Settings(LLM_PROVIDER="fake")
    client = create_llm_client_from_settings(settings)
    assert isinstance(client, FakeLLMClient)
