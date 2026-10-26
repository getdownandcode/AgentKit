"""Tests for restricting a reused read-only role (AUDIT-20).

``scripts/init_readonly_role.py`` used to change only the password of an existing role. A
pre-existing role keeps whatever it accumulated: ``SUPERUSER``, ``CREATEDB``, membership in
``pg_read_server_files`` (which allows ``pg_read_file``), or ``INSERT``/``CREATE`` grants. These
tests pin the statements that strip those privileges and the verification that refuses to
report success while any remain. The live check runs in ``tests/real/test_sql_isolation_real.py``.
"""

from __future__ import annotations

import pytest

from scripts.init_readonly_role import (
    ReadonlyRoleConfigError,
    provision_readonly_role,
    restriction_statements,
)

ROLE = "agentkit_readonly"
DATABASE = "agentkit"


def _templates() -> list[str]:
    return [template for template, _ in restriction_statements(ROLE, DATABASE)]


def test_role_attributes_are_stripped_first() -> None:
    """A superuser ignores every object-level ``REVOKE``, so attributes come first."""
    template, params = restriction_statements(ROLE, DATABASE)[0]

    assert template.startswith("ALTER ROLE %I WITH")
    for attribute in ("NOSUPERUSER", "NOCREATEDB", "NOCREATEROLE", "NOREPLICATION", "NOBYPASSRLS"):
        assert attribute in template
    assert params == {"role": ROLE}


@pytest.mark.parametrize(
    "fragment",
    [
        "REVOKE ALL PRIVILEGES ON DATABASE %I FROM %I",
        "REVOKE ALL PRIVILEGES ON SCHEMA public FROM %I",
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM %I",
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM %I",
        "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM %I",
    ],
)
def test_every_object_class_the_role_could_write_is_revoked(fragment: str) -> None:
    """Leaving one class out would let the "SELECT only" guarantee leak through it."""
    assert fragment in _templates()


def test_no_identifier_or_secret_is_interpolated_into_the_sql() -> None:
    """The role and database names travel as ``format()`` parameters, never as text."""
    statements = restriction_statements(ROLE, DATABASE)

    for template, params in statements:
        assert ROLE not in template
        assert DATABASE not in template
        assert "%I" in template
        # One statement per template: a stray separator would stack a second one.
        assert ";" not in template
        assert set(params) <= {"role", "grantee", "database"}
        assert all(value in {ROLE, DATABASE} for value in params.values())


def test_statements_are_ordered_boundaries_of_the_revoke_then_grant_cycle() -> None:
    """Attributes and object grants must be stripped before ``SELECT`` is granted back."""
    assert _templates()[0].startswith("ALTER ROLE")
    assert all(template.startswith(("ALTER ROLE", "REVOKE")) for template in _templates())


@pytest.mark.asyncio
async def test_refuses_to_provision_the_application_role() -> None:
    """Sharing the application role defeats the isolation, and is rejected before connecting."""
    with pytest.raises(ReadonlyRoleConfigError, match="application role"):
        await provision_readonly_role(
            "postgresql+asyncpg://agentkit:app_pw@postgres:5432/agentkit",
            "postgresql+asyncpg://agentkit:other_pw@postgres:5432/agentkit",
        )


@pytest.mark.asyncio
async def test_refuses_a_read_only_url_without_a_password() -> None:
    """Without a password there is nothing to provision, and guessing one is worse."""
    with pytest.raises(ReadonlyRoleConfigError, match="username and a password"):
        await provision_readonly_role(
            "postgresql+asyncpg://agentkit:app_pw@postgres:5432/agentkit",
            "postgresql+asyncpg://agentkit_readonly@postgres:5432/agentkit",
        )
