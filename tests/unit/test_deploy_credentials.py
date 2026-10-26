"""Tests for the deployment credential guard and the compose exposure defaults (AUDIT-18)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from scripts.check_deploy_credentials import audit_credentials, main

APP_URL = "postgresql+asyncpg://agentkit:app_password_9f3a@postgres:5432/agentkit"
READONLY_URL = "postgresql+asyncpg://agentkit_readonly:ro_password_7c21@postgres:5432/agentkit"
REDIS_URL = "redis://:cache_password_4b18@redis:6379/0"


def _env(**overrides: Any) -> dict[str, str]:
    values = {
        "POSTGRES_PASSWORD": "app_password_9f3a",
        "POSTGRES_READONLY_PASSWORD": "ro_password_7c21",
        "REDIS_PASSWORD": "cache_password_4b18",
        "DATABASE_URL": APP_URL,
        "DATABASE_READONLY_URL": READONLY_URL,
        "REDIS_URL": REDIS_URL,
    }
    values.update(overrides)
    return {key: value for key, value in values.items() if value is not None}


def test_specific_credentials_pass() -> None:
    """A fully specific configuration reports no problems."""
    assert audit_credentials(_env()) == []
    assert main(_env()) == 0


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("POSTGRES_PASSWORD", "postgres"),
        ("POSTGRES_PASSWORD", "change_me"),
        ("POSTGRES_READONLY_PASSWORD", "readonly"),
        ("REDIS_PASSWORD", "short"),
    ],
)
def test_default_and_placeholder_passwords_are_rejected(var: str, value: str) -> None:
    """A default, placeholder or empty secret must stop the stack."""
    problems = audit_credentials(_env(**{var: value}))
    assert any(problem.startswith(var) for problem in problems)


def test_readonly_role_must_differ_from_the_application_role() -> None:
    """Reusing the application role defeats the whole isolation model."""
    problems = audit_credentials(
        _env(DATABASE_READONLY_URL="postgresql+asyncpg://agentkit:ro_password_7c21@postgres/x"),
    )
    assert any("application role" in problem for problem in problems)


def test_readonly_role_must_not_reuse_the_application_password() -> None:
    """A shared password means one leaked credential opens both connections."""
    problems = audit_credentials(
        _env(
            DATABASE_READONLY_URL=(
                "postgresql+asyncpg://agentkit_readonly:app_password_9f3a@postgres/x"
            ),
        ),
    )
    assert any("reuses the application password" in problem for problem in problems)


def test_missing_readonly_credentials_are_reported() -> None:
    """Without a URL or the component password the SQL tool would fail closed."""
    problems = audit_credentials(
        _env(DATABASE_READONLY_URL=None, POSTGRES_READONLY_PASSWORD=None),
    )
    assert any("DATABASE_READONLY_URL is not set" in problem for problem in problems)


def test_redis_url_without_password_is_reported() -> None:
    """An unauthenticated Redis exposes session history and rate-limit counters.

    ``REDIS_URL`` may be credential-less as long as ``REDIS_PASSWORD`` supplies the
    password, so the failure case is having neither.
    """
    problems = audit_credentials(_env(REDIS_URL="redis://redis:6379/0", REDIS_PASSWORD=""))
    assert any("REDIS_URL has no password" in problem for problem in problems)


def test_readonly_url_is_accepted_from_components_after_the_url_is_dropped() -> None:
    """Dropping the URL but keeping POSTGRES_READONLY_PASSWORD is a valid configuration."""
    problems = audit_credentials(_env(DATABASE_READONLY_URL=None))
    assert problems == []


def test_redis_password_completes_a_credential_less_url() -> None:
    """The compose form (URL without credentials plus REDIS_PASSWORD) must pass the guard."""
    problems = audit_credentials(_env(REDIS_URL="redis://redis:6379/0"))
    assert problems == []


@pytest.mark.parametrize("password", ["postgres", "change_me_generate_with_openssl", "short"])
def test_weak_password_in_explicit_app_url_is_rejected(password: str) -> None:
    """A URL-supplied secret must pass the same policy as a component."""
    problems = audit_credentials(
        _env(DATABASE_URL=f"postgresql+asyncpg://agentkit:{password}@postgres:5432/agentkit")
    )
    assert any(problem.startswith("DATABASE_URL password") for problem in problems)


@pytest.mark.parametrize("password", ["readonly", "change_me", "short"])
def test_weak_password_in_explicit_readonly_url_is_rejected(password: str) -> None:
    """The read-only credential is audited even when it is not a component."""
    problems = audit_credentials(
        _env(
            DATABASE_READONLY_URL=(
                f"postgresql+asyncpg://agentkit_readonly:{password}@postgres:5432/agentkit"
            )
        )
    )
    assert any(problem.startswith("DATABASE_READONLY_URL password") for problem in problems)


def test_weak_password_in_explicit_redis_url_is_rejected() -> None:
    """REDIS_PASSWORD does not override a URL secret, so the URL secret is audited."""
    problems = audit_credentials(
        _env(REDIS_URL="redis://:change_me@redis:6379/0", REDIS_PASSWORD="cache_password_4b18")
    )
    assert any(problem.startswith("REDIS_URL password") for problem in problems)


def test_url_only_deployment_without_component_passwords_passes() -> None:
    """An explicit URL is authoritative; unused component variables must not be demanded."""
    problems = audit_credentials(
        {
            "DATABASE_URL": APP_URL,
            "DATABASE_READONLY_URL": READONLY_URL,
            "REDIS_URL": REDIS_URL,
        }
    )
    assert problems == []


def test_explicit_app_url_without_a_password_is_reported() -> None:
    """A URL with a username but no password cannot be authenticated."""
    problems = audit_credentials(
        _env(DATABASE_URL="postgresql+asyncpg://agentkit@postgres:5432/agentkit")
    )
    assert "DATABASE_URL has no password." in problems


@pytest.mark.parametrize(
    "url",
    [
        "not a url",
        "postgresql+asyncpg://agentkit:app_password_9f3a@postgres:notaport/agentkit",
    ],
)
def test_malformed_urls_are_reported_instead_of_raising(url: str) -> None:
    """A bad URL is a deployment problem, not a traceback in the migrations service."""
    problems = audit_credentials(_env(DATABASE_URL=url))
    assert "DATABASE_URL is not a valid connection URL." in problems


@pytest.mark.parametrize("url", ["redis://[broken", "redis://host:notaport/0"])
def test_malformed_redis_url_is_reported_instead_of_raising(url: str) -> None:
    """URL splitting happens before parsing, so it needs the same guard as the databases.

    ``redis://[broken`` makes ``urlsplit`` raise ``ValueError`` while ``REDIS_PASSWORD`` is
    being embedded. That used to abort the audit with a traceback before any problem line was
    printed, which is the opposite of what a fail-closed deployment guard should do.
    """
    problems = audit_credentials(_env(REDIS_URL=url))
    assert problems == ["REDIS_URL is not a valid connection URL."]


def test_problem_messages_never_echo_the_secret() -> None:
    """The guard reports variable names, never the credential it rejected."""
    secret = "hunter2"
    problems = audit_credentials(
        _env(DATABASE_URL=f"postgresql+asyncpg://agentkit:{secret}@postgres:5432/agentkit")
    )
    assert problems
    assert all(secret not in problem for problem in problems)


def test_main_logs_problems_and_exits_non_zero(caplog: pytest.LogCaptureFixture) -> None:
    """The guard exits non-zero and names each problem, so CI logs are actionable."""
    with caplog.at_level(logging.ERROR, logger="scripts.check_deploy_credentials"):
        exit_code = main(_env(POSTGRES_PASSWORD="postgres"))
    assert exit_code == 1
    assert "Refusing to continue" in caplog.text
    assert "POSTGRES_PASSWORD" in caplog.text


def test_no_print_calls_remain_in_the_guard() -> None:
    """AGENTS.md forbids print(); deployment output goes through the logger."""
    source = (Path(__file__).parents[2] / "scripts/check_deploy_credentials.py").read_text(
        encoding="utf-8"
    )
    assert "print(" not in source


def test_compose_binds_datastores_to_loopback_and_requires_passwords() -> None:
    """The shipped compose file must not publish its datastores or default its secrets."""
    compose = (Path(__file__).parents[2] / "docker-compose.yml").read_text(encoding="utf-8")

    assert '"${POSTGRES_BIND:-127.0.0.1}:5432:5432"' in compose
    assert '"${REDIS_BIND:-127.0.0.1}:6379:6379"' in compose
    assert "redis-server --requirepass" in compose
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?" in compose
    assert "REDIS_PASSWORD: ${REDIS_PASSWORD:?" in compose
    assert "python scripts/init_readonly_role.py" in compose
