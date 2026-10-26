"""Live-PostgreSQL tests for the dedicated read-only SQL role (AUDIT-01).

The unit tests can prove what the validator rejects. Only a real server can prove that the
tool's credentials are unprivileged, that the session is read-only at the database, and that a
superuser URL is refused before a query runs.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from agentkit.core.errors import ToolSecurityError
from agentkit.tools.builtin import sql_readonly as sql_tool
from agentkit.tools.builtin.sql_readonly import execute_sql_query, set_readonly_engine
from scripts.init_readonly_role import provision_readonly_role

pytestmark = pytest.mark.real_infra

READONLY_ROLE = "agentkit_readonly"
READONLY_PASSWORD = "readonly_test_password"  # noqa: S105 - dedicated test role only


def _readonly_url(admin_url: str) -> str:
    """Resolve the read-only URL, deriving one from the admin URL by default."""
    override = os.getenv("DATABASE_READONLY_TEST_URL")
    if override:
        return override
    return str(
        make_url(admin_url).set(username=READONLY_ROLE, password=READONLY_PASSWORD),
    )


@pytest.fixture
async def readonly_engine(real_database_url: str) -> AsyncIterator[AsyncEngine]:
    """Provision the dedicated role and yield an engine connected as it."""
    url = _readonly_url(real_database_url)
    try:
        await provision_readonly_role(real_database_url, url)
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"Could not provision the read-only role: {exc}")

    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_readonly_role_is_unprivileged_and_cannot_write(
    readonly_engine: AsyncEngine,
) -> None:
    """The role is not a superuser, its sessions default to read-only, and writes fail."""
    async with readonly_engine.connect() as conn:
        role: str = (await conn.execute(text("SELECT current_user"))).scalar_one()
        is_superuser: bool = (
            await conn.execute(
                text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"),
            )
        ).scalar_one()
        read_only: str = (
            await conn.execute(text("SELECT current_setting('transaction_read_only')"))
        ).scalar_one()

    assert role == READONLY_ROLE
    assert is_superuser is False
    assert read_only == "on"

    with pytest.raises(DBAPIError, match="read-only transaction"):
        async with readonly_engine.begin() as conn:
            await conn.execute(text("CREATE TABLE should_not_exist (id int)"))


async def test_readonly_role_can_select_application_tables(
    readonly_engine: AsyncEngine,
) -> None:
    """SELECT on tables created by migrations is granted, so the tool stays useful."""
    async with readonly_engine.connect() as conn:
        count: int = (await conn.execute(text("SELECT count(*) FROM runs"))).scalar_one()
    assert count >= 0


async def test_tool_refuses_a_superuser_configured_url(
    real_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pointing DATABASE_READONLY_URL at the application role fails closed."""
    monkeypatch.setattr(
        sql_tool,
        "get_settings",
        lambda: _settings(real_database_url, real_database_url),
    )
    set_readonly_engine(None)
    try:
        with pytest.raises(ToolSecurityError, match="superuser"):
            await execute_sql_query("SELECT 1")
    finally:
        set_readonly_engine(None)


async def test_tool_reads_through_the_dedicated_role(
    real_database_url: str, readonly_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default path uses the configured read-only URL end to end."""
    monkeypatch.setattr(
        sql_tool,
        "get_settings",
        lambda: _settings(real_database_url, str(readonly_engine.url)),
    )
    set_readonly_engine(None)
    try:
        output = await execute_sql_query("SELECT count(*) AS runs_count FROM runs")
    finally:
        set_readonly_engine(None)

    assert "runs_count" in output


async def test_reused_role_has_its_privileges_stripped(real_database_url: str) -> None:
    """Provisioning a pre-existing privileged role must leave it read-only.

    Reuse is the case a password-only update got wrong: the role kept ``CREATEDB`` and a
    membership in ``pg_read_server_files``, which is enough to read server files through
    ``pg_read_file`` even though the tool's ``SELECT`` grant says otherwise.
    """
    admin_engine = create_async_engine(real_database_url)
    try:
        async with admin_engine.begin() as conn:
            await conn.execute(
                text(f"ALTER ROLE {READONLY_ROLE} CREATEDB NOSUPERUSER")  # noqa: S608
            )
            await conn.execute(text(f"GRANT pg_read_server_files TO {READONLY_ROLE}"))
            createdb: bool = (
                await conn.execute(
                    text("SELECT rolcreatedb FROM pg_roles WHERE rolname = :role"),
                    {"role": READONLY_ROLE},
                )
            ).scalar_one()
        assert createdb is True, "the fixture did not actually privilege the role"

        url = _readonly_url(real_database_url)
        await provision_readonly_role(real_database_url, url)

        async with admin_engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT r.rolsuper, r.rolcreatedb, r.rolbypassrls, "
                        "ARRAY(SELECT g.rolname FROM pg_auth_members m "
                        "JOIN pg_roles g ON g.oid = m.roleid "
                        "JOIN pg_roles u ON u.oid = m.member "
                        "WHERE u.rolname = :role) "
                        "FROM pg_roles r WHERE r.rolname = :role"
                    ),
                    {"role": READONLY_ROLE},
                )
            ).one()
    finally:
        await admin_engine.dispose()

    is_superuser, can_create_db, bypasses_rls, memberships = row
    assert (is_superuser, can_create_db, bypasses_rls) == (False, False, False)
    assert list(memberships) == []


def _settings(database_url: str, readonly_url: str | None) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(DATABASE_URL=database_url, DATABASE_READONLY_URL=readonly_url)
