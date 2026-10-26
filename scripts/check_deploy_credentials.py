"""Fail closed on placeholder or default deployment credentials.

A compose stack that boots with ``postgres``/``postgres``, an unauthenticated Redis, or a
``change_me`` placeholder is indistinguishable from a working deployment until someone else
reads the data. This guard runs before migrations in the compose ``migrations`` service and
refuses to continue while any of those values are still in place.

Both credential forms are audited: the ``POSTGRES_*``/``REDIS_PASSWORD`` components and a full
URL that carries its own userinfo. The URL is resolved exactly the way
:class:`agentkit.config.Settings` resolves it (an explicit URL wins, otherwise the components
are escaped into one), so the guard inspects the credentials the application will really use.

It is deliberately a deployment concern, not a ``Settings`` validator: local development and
the hermetic test suite use throwaway credentials and must not be forced through it.

Usage::

    python scripts/check_deploy_credentials.py

Exits 0 when every credential is set and specific, 1 otherwise (with one line per problem).
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from agentkit.config import (
    DEFAULT_POSTGRES_USER,
    DEFAULT_READONLY_USER,
    apply_redis_password,
    resolve_postgres_url,
)

logger = logging.getLogger(__name__)

#: Passwords that are the image defaults, the documented examples, or a bare service name.
WEAK_PASSWORDS = frozenset(
    {
        "postgres",
        "postgresql",
        "password",
        "passwd",
        "redis",
        "changeme",
        "changeit",
        "admin",
        "root",
        "secret",
        "example",
        "agentkit",
        "readonly",
        "test",
    }
)

#: Prefixes used by the documented example values.
PLACEHOLDER_PREFIXES = ("change_me", "changeme", "your_", "replace_me", "placeholder", "todo")

#: Variables that carry a datastore password. Each is validated whenever it is set, but only
#: *required* when it is the source of the resolved URL: a deployment that pins a complete URL
#: with its own credentials does not need the components at all.
COMPONENT_PASSWORD_VARS = ("POSTGRES_PASSWORD", "POSTGRES_READONLY_PASSWORD", "REDIS_PASSWORD")

#: A short password on a loopback-only dev box is a nuisance; on a shared host it is a hole.
MIN_PASSWORD_LENGTH = 12

#: Appended to every component-password problem so the fix is one copy/paste away.
_GENERATE_HINT = "generate one with `openssl rand -base64 24`."


def _is_weak_password(value: str) -> str | None:
    """Return the reason a password is unacceptable, or None when it is specific enough."""
    lowered = value.strip().lower()
    if not lowered:
        return "is not set"
    if lowered in WEAK_PASSWORDS:
        return "is a default or well-known password"
    if lowered.startswith(PLACEHOLDER_PREFIXES):
        return "is a documented placeholder"
    if len(value.strip()) < MIN_PASSWORD_LENGTH:
        return f"is shorter than {MIN_PASSWORD_LENGTH} characters"
    return None


def _parse_url(label: str, url: str) -> tuple[URL | None, list[str]]:
    """Parse a connection URL, reporting a malformed value instead of raising.

    The parser's own exception is deliberately not reused: SQLAlchemy reproduces the string it
    could not parse, and a malformed URL is exactly where a credential is most likely to be
    visible. The caller gets a stable message naming only the variable.
    """
    try:
        return make_url(url), []
    except (ArgumentError, ValueError):
        return None, [f"{label} is not a valid connection URL."]


def _weak_password_problem(label: str, password: str | None) -> str | None:
    """Return a problem when ``password`` is absent or fails the deployment policy."""
    if not password:
        return f"{label} has no password."
    reason = _is_weak_password(password)
    if reason is not None:
        return f"{label} password {reason}."
    return None


def _password_in_url(url: str) -> str | None:
    """Return the password encoded in ``url`` itself, or None when it has none."""
    if not url:
        return None
    try:
        return make_url(url).password
    except (ArgumentError, ValueError):
        return None


def audit_credentials(env: Mapping[str, str]) -> list[str]:
    """Check the deployment's datastore credentials.

    Args:
        env: Environment mapping (normally ``os.environ``).

    Returns:
        Human-readable problems, empty when the configuration is acceptable.
    """
    problems: list[str] = []

    # A component password is validated whenever it is set, because compose always uses it to
    # create the role/database. It is only *required* when it is the source of the resolved
    # URL, so a URL-only deployment is not scolded for variables it does not use.
    for var in COMPONENT_PASSWORD_VARS:
        value = env.get(var)
        if value:
            reason = _is_weak_password(value)
            if reason is not None:
                problems.append(f"{var} {reason}; {_GENERATE_HINT}")

    explicit_app = bool((env.get("DATABASE_URL") or "").strip())
    explicit_readonly = bool((env.get("DATABASE_READONLY_URL") or "").strip())
    explicit_redis = _password_in_url((env.get("REDIS_URL") or "").strip()) is not None

    # Resolved exactly the way Settings resolves them: an explicit URL wins, otherwise the
    # URL is assembled from the POSTGRES_* components with the credentials escaped. Auditing
    # the raw variables would either miss the component form or misread an escaped password.
    app_url = (
        resolve_postgres_url(
            env,
            url_var="DATABASE_URL",
            user_var="POSTGRES_USER",
            password_var="POSTGRES_PASSWORD",
            default_user=DEFAULT_POSTGRES_USER,
        )
        or ""
    )
    readonly_url = (
        resolve_postgres_url(
            env,
            url_var="DATABASE_READONLY_URL",
            user_var="POSTGRES_READONLY_USER",
            password_var="POSTGRES_READONLY_PASSWORD",
            default_user=DEFAULT_READONLY_USER,
        )
        or ""
    )
    raw_redis = (env.get("REDIS_URL") or "").strip()
    redis_url = apply_redis_password(raw_redis, env.get("REDIS_PASSWORD"))

    app_role: str | None = None
    app_password: str | None = None
    if not app_url:
        problems.append(f"POSTGRES_PASSWORD is not set; {_GENERATE_HINT}")
    else:
        parsed_app, parse_problems = _parse_url("DATABASE_URL", app_url)
        problems.extend(parse_problems)
        if parsed_app is not None:
            app_role = parsed_app.username
            app_password = parsed_app.password
            if explicit_app:
                # The component form was validated above; only the URL's own secret remains.
                problem = _weak_password_problem("DATABASE_URL", parsed_app.password)
                if problem is not None:
                    problems.append(problem)

    if not readonly_url:
        problems.append(
            "DATABASE_READONLY_URL is not set; the sql_readonly tool would fail closed. Set "
            "POSTGRES_READONLY_PASSWORD (or the URL) so the role can be provisioned."
        )
    else:
        parsed_ro, parse_problems = _parse_url("DATABASE_READONLY_URL", readonly_url)
        problems.extend(parse_problems)
        if parsed_ro is not None:
            if not parsed_ro.username or not parsed_ro.password:
                problems.append("DATABASE_READONLY_URL must include a username and a password.")
            elif explicit_readonly:
                problem = _weak_password_problem("DATABASE_READONLY_URL", parsed_ro.password)
                if problem is not None:
                    problems.append(problem)
            if app_role and parsed_ro.username == app_role:
                problems.append(
                    "DATABASE_READONLY_URL names the application role; it must be a separate "
                    "non-superuser role."
                )
            if parsed_ro.password and app_password and parsed_ro.password == app_password:
                problems.append(
                    "DATABASE_READONLY_URL reuses the application password; give the read-only "
                    "role its own credential."
                )

    if not redis_url:
        problems.append("REDIS_URL has no password; Redis must not be reachable unauthenticated.")
    else:
        parsed_redis, parse_problems = _parse_url("REDIS_URL", redis_url)
        problems.extend(parse_problems)
        if parsed_redis is not None:
            if not parsed_redis.password:
                problems.append(
                    "REDIS_URL has no password; Redis must not be reachable unauthenticated."
                )
            elif explicit_redis:
                # REDIS_PASSWORD never overrides a password already present in the URL, so the
                # URL's own secret is the one that needs auditing here.
                problem = _weak_password_problem("REDIS_URL", parsed_redis.password)
                if problem is not None:
                    problems.append(problem)

    return problems


def main(env: Mapping[str, str] | None = None) -> int:
    """Run the audit against the environment and report the result.

    Problems are logged at ``ERROR`` so the caller's logging setup decides where they go.
    Importing this module has no side effects; the ``__main__`` block below installs the
    stderr handler used by the compose ``migrations`` service.
    """
    problems = audit_credentials(os.environ if env is None else env)
    if problems:
        logger.error("Refusing to continue: insecure deployment credentials.")
        for problem in problems:
            logger.error("  - %s", problem)
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
