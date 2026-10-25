"""Tests for the deployment credential guard and the compose exposure defaults (AUDIT-18)."""

from __future__ import annotations

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
        ("REDIS_PASSWORD", ""),
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


def test_missing_readonly_url_is_reported() -> None:
    """Without the read-only URL the SQL tool would fail closed at request time."""
    problems = audit_credentials(_env(DATABASE_READONLY_URL=None))
    assert any("DATABASE_READONLY_URL is not set" in problem for problem in problems)


def test_redis_url_without_password_is_reported() -> None:
    """An unauthenticated Redis exposes session history and rate-limit counters."""
    problems = audit_credentials(_env(REDIS_URL="redis://redis:6379/0"))
    assert any("REDIS_URL has no password" in problem for problem in problems)


def test_main_reports_problems_on_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    """The guard exits non-zero and names each problem, so CI logs are actionable."""
    exit_code = main(_env(POSTGRES_PASSWORD="postgres"))
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Refusing to continue" in captured.err
    assert "POSTGRES_PASSWORD" in captured.err


def test_compose_binds_datastores_to_loopback_and_requires_passwords() -> None:
    """The shipped compose file must not publish its datastores or default its secrets."""
    compose = (Path(__file__).parents[2] / "docker-compose.yml").read_text(encoding="utf-8")

    assert '"${POSTGRES_BIND:-127.0.0.1}:5432:5432"' in compose
    assert '"${REDIS_BIND:-127.0.0.1}:6379:6379"' in compose
    assert "redis-server --requirepass" in compose
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?" in compose
    assert "REDIS_PASSWORD: ${REDIS_PASSWORD:?" in compose
    assert "python scripts/init_readonly_role.py" in compose
