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

Re-using an existing role is handled as a restriction, not a password change: attributes
(``SUPERUSER``, ``CREATEDB``, ``CREATEROLE``, ``REPLICATION``, ``BYPASSRLS``), every inherited
role membership and every non-``SELECT`` object privilege are revoked before the grants are
applied. A role that cannot be made read-only — the database owner, for instance, which can
grant itself anything — is refused with :class:`ReadonlyRoleConfigError` instead of being
reported as provisioned.
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

#: Attributes a read-only role must not hold. ``BYPASSRLS`` in particular would let a
#: ``SELECT`` see rows that row-level security hides.
_SAFE_ATTRIBUTES = "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"


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


def restriction_statements(role: str, database: str) -> list[tuple[str, dict[str, str]]]:
    """Return the statements that strip everything but reading from an existing role.

    Re-using a role that already exists is the dangerous case: the script used to change only
    its password, leaving attributes and memberships alone. A role that is a superuser, that
    inherits ``pg_read_server_files`` or that holds ``GRANT``/``CREATE`` keeps those powers, so
    the ``SELECT``-only grant described in the module docstring would be a fiction. Each
    statement is idempotent and quoted server-side with ``format()``, like every other
    statement this script runs.

    Args:
        role: Role to restrict.
        database: Database the role connects to.

    Returns:
        ``(format template, format parameters)`` pairs, to be executed with
        :func:`_run_formatted`.
    """
    return [
        # Attributes first: a superuser ignores every object privilege below.
        (f"ALTER ROLE %I WITH {_SAFE_ATTRIBUTES}", {"role": role}),
        # Database: CONNECT is re-granted afterwards; CREATE and TEMPORARY are not.
        ("REVOKE ALL PRIVILEGES ON DATABASE %I FROM %I", {"database": database, "grantee": role}),
        # Schema: USAGE is re-granted afterwards; CREATE is not.
        ("REVOKE ALL PRIVILEGES ON SCHEMA public FROM %I", {"grantee": role}),
        # Objects: SELECT is re-granted afterwards; everything else (INSERT/UPDATE/TRUNCATE,
        # sequence writes) is dropped. REVOKE only removes grants this role holds directly,
        # which is why inherited memberships are revoked separately.
        (
            "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM %I",
            {"grantee": role},
        ),
        (
            "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM %I",
            {"grantee": role},
        ),
        (
            "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM %I",
            {"grantee": role},
        ),
    ]


async def _revoke_memberships(conn: AsyncConnection, role: str) -> None:
    """Revoke every role membership the target role holds.

    Memberships are the part object-level ``REVOKE`` cannot reach: a member of
    ``pg_read_server_files`` can call ``pg_read_file`` no matter what the role itself was
    granted. Dropping every direct membership leaves the role with only its own privileges,
    and an indirect grant requires a direct one to start from.
    """
    granted_roles = list(
        (
            await conn.execute(
                text(
                    "SELECT g.rolname FROM pg_auth_members m "
                    "JOIN pg_roles g ON g.oid = m.roleid "
                    "JOIN pg_roles u ON u.oid = m.member "
                    "WHERE u.rolname = :role"
                ),
                {"role": role},
            )
        ).scalars()
    )
    for granted in granted_roles:
        logger.info("Revoking inherited membership %s from read-only role '%s'.", granted, role)
        await _run_formatted(conn, "REVOKE %I FROM %I", granted=granted, grantee=role)


async def _assert_restricted(conn: AsyncConnection, role: str) -> None:
    """Verify the role really is unprivileged, and fail rather than trust the statements.

    Raises:
        ReadonlyRoleConfigError: If the role kept an attribute or a membership that makes its
            ``SELECT`` connection more than read-only.
    """
    row = (
        await conn.execute(
            text(
                "SELECT r.rolsuper, r.rolcreatedb, r.rolcreaterole, r.rolreplication, "
                "r.rolbypassrls, "
                "ARRAY(SELECT g.rolname FROM pg_auth_members m "
                "JOIN pg_roles g ON g.oid = m.roleid "
                "JOIN pg_roles u ON u.oid = m.member "
                "WHERE u.rolname = :role) AS memberships "
                "FROM pg_roles r WHERE r.rolname = :role"
            ),
            {"role": role},
        )
    ).one_or_none()
    if row is None:
        raise ReadonlyRoleConfigError(f"Role '{role}' does not exist after provisioning.")

    is_superuser, can_create_db, can_create_role, replicates, bypasses_rls, memberships = row
    overprivileged = [
        name
        for name, enabled in (
            ("SUPERUSER", is_superuser),
            ("CREATEDB", can_create_db),
            ("CREATEROLE", can_create_role),
            ("REPLICATION", replicates),
            ("BYPASSRLS", bypasses_rls),
        )
        if enabled
    ]
    if overprivileged or memberships:
        detail = ", ".join(overprivileged + [f"member of {m}" for m in memberships or []])
        raise ReadonlyRoleConfigError(
            f"Role '{role}' is still privileged after provisioning ({detail}). Point "
            "DATABASE_READONLY_URL at a role that can be restricted, or revoke those "
            "privileges manually."
        )


async def provision_readonly_role(admin_url: str, readonly_url: str) -> str:
    """Create or update the read-only role and grant it read access.

    An existing role is restricted before it is granted anything: attributes and inherited
    memberships are stripped, and every object privilege is revoked before ``SELECT`` is
    granted back. The result is verified, so a role that cannot be made read-only raises
    instead of silently keeping its powers.

    Args:
        admin_url: Async SQLAlchemy URL for a role that may create roles and grant privileges.
        readonly_url: Async SQLAlchemy URL for the dedicated read-only role.

    Returns:
        The provisioned role name.

    Raises:
        ReadonlyRoleConfigError: If the read-only URL has no username/password, names the same
            role as the admin URL, names the database owner, or cannot be restricted.
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

            # The owner of a database can grant itself anything, so no statement below could
            # make it read-only. Refuse instead of reporting success for an unsafe role.
            owner = (
                await conn.execute(
                    text(
                        "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = :database"
                    ),
                    {"database": database},
                )
            ).scalar_one_or_none()
            if owner == role:
                raise ReadonlyRoleConfigError(
                    f"DATABASE_READONLY_URL names '{role}', the owner of database "
                    f"'{database}'. An owner can grant itself any privilege, so it can never "
                    "be read-only. Use a dedicated non-owner role."
                )

            # Strip everything a ``SELECT`` role must not have before granting it anything.
            for template, params in restriction_statements(role, database):
                await _run_formatted(conn, template, **params)
            await _revoke_memberships(conn, role)

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

            # Prove it, rather than trusting that every statement above did what it says.
            await _assert_restricted(conn, role)
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
