"""Unit tests for Redis credential resolution shared by the app and the real-infra suite.

The compose stack starts Redis with ``--requirepass``, so any credential-less URL fails
with ``HELLO must be called with the client already authenticated``. These tests pin the
completion rules without needing a live Redis.
"""

from __future__ import annotations

import pytest

import tests.conftest as conftest
from agentkit.config import Settings, apply_redis_password


def _settings(**overrides: object) -> Settings:
    """Build Settings with the mandatory key so validation succeeds."""
    values: dict[str, object] = {"API_KEYS": "test_key"}
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_password_is_embedded_when_url_has_no_credentials() -> None:
    assert (
        apply_redis_password("redis://localhost:6379/15", "s3cret")
        == "redis://:s3cret@localhost:6379/15"
    )


def test_existing_credentials_are_never_overridden() -> None:
    url = "redis://:original@localhost:6379/0"
    assert apply_redis_password(url, "replacement") == url


def test_username_only_url_is_left_alone() -> None:
    url = "redis://someone@localhost:6379/0"
    assert apply_redis_password(url, "replacement") == url


def test_no_password_leaves_url_untouched() -> None:
    url = "redis://localhost:6379/0"
    assert apply_redis_password(url, None) == url
    assert apply_redis_password(url, "") == url


def test_reserved_characters_in_password_are_percent_encoded() -> None:
    assert (
        apply_redis_password("redis://localhost:6379/0", "p@ss/word?")
        == "redis://:p%40ss%2Fword%3F@localhost:6379/0"
    )


@pytest.mark.parametrize("url", ["rediss://redis.internal:6380/1", "redis://[::1]:6379/0"])
def test_other_redis_schemes_and_ipv6_hosts_are_supported(url: str) -> None:
    completed = apply_redis_password(url, "s3cret")
    assert "@" in completed
    assert "s3cret" in completed


def test_non_redis_url_is_returned_unchanged() -> None:
    url = "postgresql+asyncpg://postgres:postgres@localhost:5432/agentkit"
    assert apply_redis_password(url, "s3cret") == url


def test_malformed_url_without_netloc_is_left_alone() -> None:
    assert apply_redis_password("redis://", "s3cret") == "redis://"


def test_settings_completes_redis_url_from_password() -> None:
    settings = _settings(REDIS_URL="redis://localhost:6379/0", REDIS_PASSWORD="pw-from-env")
    assert settings.REDIS_URL == "redis://:pw-from-env@localhost:6379/0"


def test_settings_keeps_url_credentials_when_both_are_set() -> None:
    settings = _settings(
        REDIS_URL="redis://:explicit@localhost:6379/0",
        REDIS_PASSWORD="pw-from-env",
    )
    assert settings.REDIS_URL == "redis://:explicit@localhost:6379/0"


def test_settings_without_password_matches_documented_default() -> None:
    settings = _settings()
    assert settings.REDIS_PASSWORD is None
    assert settings.REDIS_URL == "redis://localhost:6379/0"


def test_real_suite_url_prefers_explicit_test_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_TEST_URL", "redis://localhost:6379/15")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.delenv("REDIS_PASSWORD", raising=False)

    assert conftest.redis_test_url() == "redis://localhost:6379/15"


def test_real_suite_url_authenticates_a_bare_test_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_TEST_URL", "redis://localhost:6379/15")
    monkeypatch.setenv("REDIS_PASSWORD", "compose_secret")

    assert conftest.redis_test_url() == "redis://:compose_secret@localhost:6379/15"


def test_real_suite_url_leaves_disclosed_credentials_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_TEST_URL", "redis://:ci_pass@localhost:6379/15")
    monkeypatch.setenv("REDIS_PASSWORD", "compose_secret")

    assert conftest.redis_test_url() == "redis://:ci_pass@localhost:6379/15"


def test_real_suite_url_falls_back_to_settings_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """A password defined only in ``.env`` reaches the suite through Settings."""
    monkeypatch.delenv("REDIS_TEST_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_PASSWORD", raising=False)
    monkeypatch.setattr(
        conftest,
        "get_settings",
        lambda: _settings(REDIS_URL="redis://localhost:6379/9", REDIS_PASSWORD="from_dotenv"),
    )

    assert conftest.redis_test_url() == "redis://:from_dotenv@localhost:6379/9"
