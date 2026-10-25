"""Provision the dedicated read-only PostgreSQL role used by the ``sql_readonly`` tool.

The tool must never run with the application's own database credentials. This script creates
(or updates) the role named in ``DATABASE_READONLY_URL`` and grants it exactly what a
read-only query needs: connect, schema usage and ``SELECT`` on the tables — plus
``default_transaction_read_only`` so a stray statement cannot write even if a future grant
widens by accident.

Run it with the application's admin URL, after migrations have created the tables (the
compose ``migrations`` service does this automatically)::

    python scripts/init_readonly_role.py

Every statement is idempotent, so a restart or a re-run after new tables appear is safe. The
role name and password are quoted server-side with PostgreSQL's own ``format()`` — no
credential is ever interpolated into SQL by this script.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from agentkit.config import get_settings

logger = logging.getLogger(__name__)


class ReadonlyRoleConfigError(Exception):
    """Raised when the read-only role cannot be provisioned from the given configuration."""


def _role_from_url(readonly_url: str) -> tuple[str, str]:
    """Extract and validate the role name and password from a connection URL."""
    parsed = make_url(readonly_url)
    role = parsed.username
    password = parsed.password
    if not role or not password:
        raise ReadonlyRoleConfigError(
            "DATABASE_READONLY_URL must include both a username and a password for the "
            "dedicated read-only role."
        )
    return role, password


async def _run_formatted(
    conn: AsyncConnection,
    template: str,
    **params: str,
) -> None:
    """Execute DDL built with server-side ``format()`` quoting (``%I`` for identifiers).

    ``format()`` runs on the server and quotes the substituted values, so a role name or
    password containing quotes cannot break out of the statement. Connection URLs are user
    input and must never be pasted into SQL directly.
    """
    # Explicit text casts: format() takes variadic "any" arguments, so asyncpg cannot
    # infer a parameter type and the statement fails to prepare without them.
    placeholders = ", ".join(f"CAST(:{key} AS text)" for key in params)
    built = (
        await conn.execute(
            text(f"SELECT format(:template, {placeholders})"),
            {"template": template, **params},
        )
    ).scalar_one()
    await conn.execute(text(str(built)))


async def provision_readonly_role(admin_url: str, readonly_url: str) -> str:
    """Create or update the read-only role and grant it read access.

    Args:
        admin_url: Async SQLAlchemy URL for a role that may create roles and grant privileges.
        readonly_url: Async SQLAlchemy URL for the dedicated read-only role.

    Returns:
        The provisioned role name.

    Raises:
        ReadonlyRoleConfigError: If the read-only URL has no username/password, or names the
            same role as the admin URL.
    """
    role, password = _role_from_url(readonly_url)
    parsed_admin = make_url(admin_url)
    admin_role = parsed_admin.username
    database = parsed_admin.database
    if admin_role == role:
        raise ReadonlyRoleConfigError(
            f"DATABASE_READONLY_URL names the application role '{role}'. It must name a "
            "separate, non-superuser role."
        )
    if not database:
        raise ReadonlyRoleConfigError("DATABASE_URL must include a database name.")

    engine = create_async_engine(admin_url)
    try:
        async with engine.begin() as conn:
            exists = (
                await conn.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
                    {"role": role},
                )
            ).first()
            verb = "ALTER" if exists else "CREATE"
            await _run_formatted(
                conn,
                f"{verb} ROLE %I WITH LOGIN PASSWORD %L",
                role=role,
                password=password,
            )

            # A role-level default covers every new session, including the ones opened by a
            # connection pool that outlives this provisioning run.
            await _run_formatted(
                conn,
                "ALTER ROLE %I SET default_transaction_read_only = on",
                role=role,
            )
            await _run_formatted(
                conn,
                "GRANT CONNECT ON DATABASE %I TO %I",
                database=database,
                grantee=role,
            )
            await _run_formatted(
                conn,
                "GRANT USAGE ON SCHEMA public TO %I",
                grantee=role,
            )
            await _run_formatted(
                conn,
                "GRANT SELECT ON ALL TABLES IN SCHEMA public TO %I",
                grantee=role,
            )
            # Tables created by future migrations get the same grant automatically.
            await _run_formatted(
                conn,
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO %I",
                grantee=role,
            )
    finally:
        await engine.dispose()

    logger.info("Read-only role '%s' provisioned with SELECT access only.", role)
    return role


async def _main() -> int:
    """Provision the role from application settings."""
    settings = get_settings()
    if not settings.DATABASE_READONLY_URL:
        logger.error("DATABASE_READONLY_URL is not set; nothing to provision.")
        return 1
    await provision_readonly_role(settings.DATABASE_URL, settings.DATABASE_READONLY_URL)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(asyncio.run(_main()))
