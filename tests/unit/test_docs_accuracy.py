"""Guards that keep documentation claims aligned with the code (AUDIT-16).

These are deliberately narrow checks: each one pins a statement that was false in the past and
that nothing in CI would otherwise notice.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentkit.config import Settings

ROOT = Path(__file__).parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_default_gemini_model_in_docs_matches_settings() -> None:
    """The model named in the docs must be the one the code actually defaults to."""
    default_model = Settings.model_fields["LLM_MODEL"].default
    assert isinstance(default_model, str)

    for document in ("README.md", "docs/ARCHITECTURE.md", ".env.example"):
        assert default_model in _read(document), f"{document} does not name {default_model}"


def test_readme_does_not_claim_ast_validation_for_sql() -> None:
    """The SQL tool uses a lexer plus a dedicated role, not an AST parser."""
    readme = _read("README.md")
    sql_line = next(
        line for line in readme.splitlines() if line.strip().startswith("- `sql_readonly`")
    )

    assert "AST" not in sql_line
    assert "DATABASE_READONLY_URL" in sql_line


def test_adr_006_is_marked_superseded_and_corrected() -> None:
    """ADR-006 asserted a base64 behaviour Pydantic never had and a stale model id."""
    decisions = _read("docs/DECISIONS.md")
    adr_006_start = decisions.index("## ADR-006")
    adr_006_end = decisions.index("## ADR-007")
    adr_006 = decisions[adr_006_start:adr_006_end]

    assert "superseded in part by ADR-008" in adr_006
    assert "ADR-013" in adr_006
    assert "**Correction:**" in adr_006
    assert "gemini-3.1-flash-lite" in adr_006


def test_architecture_read_only_sql_claim_matches_the_implementation() -> None:
    """The guardrail section must describe the real control, not an aspiration."""
    architecture = _read("docs/ARCHITECTURE.md")
    bullet = next(
        line for line in architecture.splitlines() if line.startswith("- **Read-Only SQL**")
    )

    assert "DATABASE_READONLY_URL" in bullet
    assert "default_transaction_read_only" in bullet
    assert "lexer" in bullet


def test_audit_table_records_the_sql_and_gemini_findings() -> None:
    """The component table must not claim an unqualified PASS for the fixed components."""
    audit = _read("docs/AUDIT.md")

    assert "**PASS (after AUDIT-01/02)**" in audit
    assert "**PASS (after AUDIT-04/05/06)**" in audit


def test_progress_no_longer_claims_ast_validation_for_sql() -> None:
    """The historical M4 row must not contradict the corrected README."""
    progress = _read("docs/PROGRESS.md")
    row = next(line for line in progress.splitlines() if line.startswith("| M4-T04"))
    assert "AST" not in row


def test_audit_history_uses_the_canonical_stack_description() -> None:
    """The merged stack is 12 layers / 14 commits; earlier counts had drifted."""
    audit = _read("docs/AUDIT.md")
    assert "12-layer stack" in audit
    assert "14 commits" in audit
    assert "nine branches" not in audit


def test_every_audit_finding_appears_in_the_remediation_table() -> None:
    """A closed finding that is missing from the table is invisible to reviewers."""
    audit = _read("docs/AUDIT.md")
    for number in range(1, 19):
        assert re.search(rf"\bAUDIT-{number:02d}\b", audit), f"AUDIT-{number:02d} is not recorded"
