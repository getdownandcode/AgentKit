"""Fail closed on placeholder or default deployment credentials.

A compose stack that boots with ``postgres``/``postgres``, an unauthenticated Redis, or a
``change_me`` placeholder is indistinguishable from a working deployment until someone else
reads the data. This guard runs before migrations in the compose ``migrations`` service and
refuses to continue while any of those values are still in place.

It is deliberately a deployment concern, not a ``Settings`` validator: local development and
the hermetic test suite use throwaway credentials and must not be forced through it.

Usage::

    python scripts/check_deploy_credentials.py

Exits 0 when every credential is set and specific, 1 otherwise (with one line per problem).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

from sqlalchemy.engine import make_url

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

#: Secrets the compose stack depends on.
REQUIRED_PASSWORD_VARS = ("POSTGRES_PASSWORD", "POSTGRES_READONLY_PASSWORD", "REDIS_PASSWORD")

#: A short password on a loopback-only dev box is a nuisance; on a shared host it is a hole.
MIN_PASSWORD_LENGTH = 12


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


def audit_credentials(env: Mapping[str, str]) -> list[str]:
    """Check the deployment's datastore credentials.

    Args:
        env: Environment mapping (normally ``os.environ``).

    Returns:
        Human-readable problems, empty when the configuration is acceptable.
    """
    problems: list[str] = []

    for var in REQUIRED_PASSWORD_VARS:
        reason = _is_weak_password(env.get(var, ""))
        if reason is not None:
            problems.append(f"{var} {reason}; generate one with `openssl rand -base64 24`.")

    app_url = env.get("DATABASE_URL", "")
    readonly_url = env.get("DATABASE_READONLY_URL", "")

    app_role: str | None = None
    if app_url:
        parsed_app = make_url(app_url)
        app_role = parsed_app.username
        if parsed_app.password and parsed_app.password in WEAK_PASSWORDS:
            problems.append("DATABASE_URL uses a default password.")

    if readonly_url:
        parsed_ro = make_url(readonly_url)
        if not parsed_ro.username or not parsed_ro.password:
            problems.append("DATABASE_READONLY_URL must include a username and a password.")
        if app_role and parsed_ro.username == app_role:
            problems.append(
                "DATABASE_READONLY_URL names the application role; it must be a separate "
                "non-superuser role."
            )
        if parsed_ro.password and app_url and parsed_ro.password == make_url(app_url).password:
            problems.append(
                "DATABASE_READONLY_URL reuses the application password; give the read-only "
                "role its own credential."
            )
    else:
        problems.append(
            "DATABASE_READONLY_URL is not set; the sql_readonly tool would fail closed."
        )

    redis_url = env.get("REDIS_URL", "")
    if redis_url and not make_url(redis_url).password:
        problems.append("REDIS_URL has no password; Redis must not be reachable unauthenticated.")

    return problems


def main(env: Mapping[str, str] | None = None) -> int:
    """Run the audit against the environment and report the result."""
    problems = audit_credentials(os.environ if env is None else env)
    if problems:
        print("Refusing to continue: insecure deployment credentials.", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
