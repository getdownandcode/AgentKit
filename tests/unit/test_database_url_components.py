"""Tests for assembling connection URLs from components (AUDIT-19).

Compose used to interpolate ``POSTGRES_PASSWORD`` straight into a ``DATABASE_URL``. The
documented generator is ``openssl rand -base64 24``, whose output contains ``/``, ``+`` and
``=``: a ``/`` ends the URL authority, so the rest of the password becomes the path and the
connection targets a database that does not exist. These tests pin the component form, the
escaping, and the precedence rules that make an explicit URL authoritative.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from sqlalchemy.engine import make_url

from agentkit.config import (
    DEFAULT_DATABASE_URL,
    Settings,
    build_postgres_url,
    resolve_postgres_url,
)
from scripts.check_deploy_credentials import audit_credentials

ROOT = Path(__file__).parents[2]

#: A password from ``openssl rand -base64 24``: every character that breaks a URL.
GENERATED_PASSWORD = "aB3/xY+9zQ==wE7/tY+4uI"


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the ambient environment out of these tests.

    They assert precedence between an explicit URL and the components, so a ``DATABASE_URL``
    exported by CI (or sitting in a developer's shell) would decide the outcome instead of
    the test. ``_env_file=None`` covers the dotenv source; this covers the process environment.
    """
    for name in (
        "DATABASE_URL",
        "DATABASE_READONLY_URL",
        "REDIS_URL",
        "REDIS_PASSWORD",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "POSTGRES_READONLY_USER",
        "POSTGRES_READONLY_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)


def _build(env_file: Path | None, **values: object) -> Settings:
    """Construct Settings from explicit values, optionally through an env file.

    ``_env_file`` is a documented pydantic-settings init argument that the bundled type
    stubs do not declare, and the ``**values`` mapping is intentionally untyped because the
    tests name settings as strings; both are expected here.
    """
    arguments: dict[str, Any] = {"API_KEYS": "k", **values}
    return Settings(_env_file=env_file, **arguments)  # type: ignore[call-arg]


def _settings(**overrides: object) -> Settings:
    """Build Settings without reading a developer's ``.env``."""
    return _build(None, **overrides)


# --- build_postgres_url ----------------------------------------------------------------------


def test_generated_password_survives_the_round_trip() -> None:
    """``/``, ``+`` and ``=`` are escaped, so the parsed password is the original value."""
    url = build_postgres_url(
        host="postgres",
        port=5432,
        database="agentkit",
        username="agentkit",
        password=GENERATED_PASSWORD,
    )

    assert "/" not in url.split("://", 1)[1].split("@", 1)[0].replace("postgres/", "")
    parsed = make_url(url)
    assert parsed.password == GENERATED_PASSWORD
    assert parsed.username == "agentkit"
    assert parsed.host == "postgres"
    assert parsed.port == 5432
    assert parsed.database == "agentkit"


def _naive_url(password: str) -> str:
    """The interpolation this change removes: credentials pasted into the URL string."""
    return f"postgresql+asyncpg://agentkit:{password}@postgres:5432/agentkit"


def test_rfc_parsers_split_the_authority_at_an_unencoded_slash() -> None:
    """``urlsplit`` ends the authority at ``/``, so the password becomes the path.

    SQLAlchemy happens to re-parse this leniently, but every RFC 3986 consumer (and
    :func:`agentkit.config.apply_redis_password`) splits it here instead.
    """
    split = urlsplit(_naive_url(GENERATED_PASSWORD))
    assert split.netloc == "agentkit:aB3"
    assert split.path != "/agentkit"

    escaped = build_postgres_url(
        host="postgres",
        port=5432,
        database="agentkit",
        username="agentkit",
        password=GENERATED_PASSWORD,
    )
    assert urlsplit(escaped).path == "/agentkit"


def test_an_at_sign_breaks_even_the_lenient_parser() -> None:
    """SQLAlchemy keeps only the text before the last ``@``, silently truncating a secret."""
    password = "p@ss/w+rd"
    truncated = make_url(_naive_url(password))
    assert truncated.password == "p"
    assert truncated.host != "postgres"

    escaped = build_postgres_url(
        host="postgres",
        port=5432,
        database="agentkit",
        username="agentkit",
        password=password,
    )
    assert make_url(escaped).password == password


# --- resolve_postgres_url --------------------------------------------------------------------


def test_explicit_url_wins_over_components() -> None:
    """A pinned URL (managed database, secret substitution) must not be rebuilt."""
    url = resolve_postgres_url(
        {
            "DATABASE_URL": "postgresql+asyncpg://pinned:pw@db.internal:6543/prod",
            "POSTGRES_PASSWORD": "ignored_password",
            "POSTGRES_HOST": "postgres",
        },
        url_var="DATABASE_URL",
        user_var="POSTGRES_USER",
        password_var="POSTGRES_PASSWORD",
        default_user="postgres",
    )
    assert url == "postgresql+asyncpg://pinned:pw@db.internal:6543/prod"


def test_components_build_the_url_with_defaults_for_anything_omitted() -> None:
    """Only the password is required; the rest falls back to the documented defaults."""
    url = resolve_postgres_url(
        {"POSTGRES_PASSWORD": GENERATED_PASSWORD},
        url_var="DATABASE_URL",
        user_var="POSTGRES_USER",
        password_var="POSTGRES_PASSWORD",
        default_user="postgres",
    )

    assert url is not None
    parsed = make_url(url)
    assert parsed.host == "localhost"
    assert parsed.port == 5432
    assert parsed.database == "agentkit"
    assert parsed.username == "postgres"
    assert parsed.password == GENERATED_PASSWORD


def test_components_honour_every_overridden_value() -> None:
    """Host, port, database and role all come from their own variables."""
    url = resolve_postgres_url(
        {
            "POSTGRES_HOST": "db.internal",
            "POSTGRES_PORT": "6543",
            "POSTGRES_DB": "prod",
            "POSTGRES_USER": "svc_agentkit",
            "POSTGRES_PASSWORD": GENERATED_PASSWORD,
        },
        url_var="DATABASE_URL",
        user_var="POSTGRES_USER",
        password_var="POSTGRES_PASSWORD",
        default_user="postgres",
    )

    assert url is not None
    parsed = make_url(url)
    assert (parsed.host, parsed.port, parsed.database, parsed.username) == (
        "db.internal",
        6543,
        "prod",
        "svc_agentkit",
    )


def test_no_url_and_no_password_yields_none() -> None:
    """Without either form there is nothing to resolve, and no URL is invented."""
    assert (
        resolve_postgres_url(
            {"POSTGRES_HOST": "postgres"},
            url_var="DATABASE_URL",
            user_var="POSTGRES_USER",
            password_var="POSTGRES_PASSWORD",
            default_user="postgres",
        )
        is None
    )


def test_non_integer_port_is_a_configuration_error() -> None:
    """A typo must be reported, not silently treated as the default port."""
    with pytest.raises(ValueError, match="POSTGRES_PORT"):
        resolve_postgres_url(
            {"POSTGRES_PASSWORD": "pw", "POSTGRES_PORT": "port"},
            url_var="DATABASE_URL",
            user_var="POSTGRES_USER",
            password_var="POSTGRES_PASSWORD",
            default_user="postgres",
        )


# --- Settings integration --------------------------------------------------------------------


def test_settings_build_both_urls_from_components() -> None:
    """Application and read-only roles are assembled separately from their own variables."""
    settings = _settings(
        POSTGRES_HOST="postgres",
        POSTGRES_PORT=5432,
        POSTGRES_USER="agentkit",
        POSTGRES_PASSWORD=GENERATED_PASSWORD,
        POSTGRES_DB="agentkit",
        POSTGRES_READONLY_USER="agentkit_readonly",
        POSTGRES_READONLY_PASSWORD="ro/+" + GENERATED_PASSWORD,
    )

    assert make_url(settings.DATABASE_URL).password == GENERATED_PASSWORD
    assert make_url(settings.DATABASE_URL).username == "agentkit"
    assert settings.DATABASE_READONLY_URL is not None
    readonly = make_url(settings.DATABASE_READONLY_URL)
    assert readonly.username == "agentkit_readonly"
    assert readonly.password == "ro/+" + GENERATED_PASSWORD
    assert readonly.database == "agentkit"


def test_settings_keep_an_explicit_url_even_with_components_set() -> None:
    """The escape hatch stays authoritative, so existing deployments are unaffected."""
    pinned = "postgresql+asyncpg://pinned:pw@db.internal:6543/prod"
    settings = _settings(DATABASE_URL=pinned, POSTGRES_PASSWORD=GENERATED_PASSWORD)

    assert pinned == settings.DATABASE_URL


def test_settings_without_components_keep_the_documented_defaults() -> None:
    """A bare ``Settings`` must still describe the development database."""
    settings = _settings()

    assert settings.DATABASE_URL == DEFAULT_DATABASE_URL
    assert settings.DATABASE_READONLY_URL is None


def test_settings_read_the_components_from_an_env_file(tmp_path: Path) -> None:
    """The documented ``.env`` form works, not just process environment variables."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"POSTGRES_PASSWORD={GENERATED_PASSWORD}\n"
        "POSTGRES_READONLY_PASSWORD=readonly_value\n"
        "POSTGRES_DB=from_file\n",
        encoding="utf-8",
    )

    settings = _build(env_file)

    assert make_url(settings.DATABASE_URL).password == GENERATED_PASSWORD
    assert make_url(settings.DATABASE_URL).database == "from_file"
    assert settings.DATABASE_READONLY_URL is not None
    assert make_url(settings.DATABASE_READONLY_URL).password == "readonly_value"


def test_settings_reject_a_non_integer_component_port() -> None:
    """The component form is validated like any other typed setting."""
    with pytest.raises(ValueError, match="POSTGRES_PORT"):
        _settings(POSTGRES_PASSWORD="pw", POSTGRES_PORT="not-a-port")


# --- credential guard ------------------------------------------------------------------------


def _component_env(**overrides: str) -> dict[str, str]:
    env = {
        "POSTGRES_HOST": "postgres",
        "POSTGRES_PORT": "5432",
        "POSTGRES_USER": "agentkit",
        "POSTGRES_PASSWORD": GENERATED_PASSWORD,
        "POSTGRES_DB": "agentkit",
        "POSTGRES_READONLY_USER": "agentkit_readonly",
        "POSTGRES_READONLY_PASSWORD": "readonly" + GENERATED_PASSWORD,
        "REDIS_URL": "redis://redis:6379/0",
        "REDIS_PASSWORD": "cache" + GENERATED_PASSWORD,
    }
    env.update(overrides)
    return env


def test_guard_accepts_the_component_form_with_url_breaking_passwords() -> None:
    """The composed stack's configuration must pass the guard, escaped passwords included."""
    assert audit_credentials(_component_env()) == []


def test_guard_still_rejects_a_weak_component_password() -> None:
    """Component passwords get the same strength checks as hand-written URLs."""
    problems = audit_credentials(_component_env(POSTGRES_PASSWORD="postgres"))
    assert any(problem.startswith("POSTGRES_PASSWORD") for problem in problems)


def test_guard_reports_a_missing_readonly_role_in_the_component_form() -> None:
    """Without the read-only password nothing can provision the role."""
    problems = audit_credentials(_component_env(POSTGRES_READONLY_PASSWORD=""))
    assert any("DATABASE_READONLY_URL is not set" in problem for problem in problems)


def test_guard_reports_a_credential_less_redis_url() -> None:
    """``REDIS_PASSWORD`` is what authenticates; an empty one leaves Redis open."""
    problems = audit_credentials(_component_env(REDIS_PASSWORD=""))
    assert any("REDIS_URL has no password" in problem for problem in problems)


def test_guard_and_settings_agree_on_the_readonly_role_name() -> None:
    """A guard that audited a different role than Settings used would be worthless."""
    env = _component_env(POSTGRES_READONLY_USER="", POSTGRES_READONLY_PASSWORD="ro_password_9h2x")
    assert audit_credentials(env) == []
    settings = _settings(
        POSTGRES_READONLY_USER="",
        POSTGRES_READONLY_PASSWORD="ro_password_9h2x",
    )
    assert settings.DATABASE_READONLY_URL is not None
    assert make_url(settings.DATABASE_READONLY_URL).username == "agentkit_readonly"


# --- compose ---------------------------------------------------------------------------------


def test_compose_passes_credentials_as_components_not_urls() -> None:
    """The shipped compose file must not interpolate a secret into a URL again."""
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "${POSTGRES_PASSWORD}@" not in compose
    assert "${POSTGRES_READONLY_PASSWORD}@" not in compose
    assert "${REDIS_PASSWORD}@redis" not in compose
    assert "POSTGRES_HOST=postgres" in compose
    assert "POSTGRES_READONLY_PASSWORD=${POSTGRES_READONLY_PASSWORD" in compose
    assert "REDIS_URL=redis://redis:6379/0" in compose
    assert "REDIS_PASSWORD=${REDIS_PASSWORD}" in compose


def test_env_example_does_not_teach_hand_built_urls() -> None:
    """A documented URL containing a password is the pattern that caused the bug."""
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    active = [line for line in example.splitlines() if line.strip() and not line.startswith("#")]

    assert not [line for line in active if line.startswith("DATABASE_URL=")]
    assert not [line for line in active if line.startswith("DATABASE_READONLY_URL=")]
    assert any(line.startswith("POSTGRES_PASSWORD=") for line in active)
    assert any(line.startswith("POSTGRES_READONLY_PASSWORD=") for line in active)
