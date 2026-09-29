from __future__ import annotations

from pathlib import Path

import pytest

from agentkit.tools.builtin.calculator import safe_calculate
from agentkit.tools.builtin.http_fetch import validate_url_ssrf
from agentkit.tools.builtin.read_file import read_sandboxed_file
from agentkit.tools.builtin.sql_readonly import validate_sql_query


def test_calculator_ast_evasion_blocked() -> None:
    """Verify that dangerous Python builtins, imports, and arbitrary execution are blocked by AST validator."""
    dangerous_payloads = [
        "__import__('os').system('ls')",
        "__import__('sys').exit(1)",
        "open('/etc/passwd').read()",
        "(1).__class__.__bases__[0].__subclasses__()",
        "eval('1 + 1')",
        "exec('a = 1')",
        "compile('1', '', 'eval')",
        "getattr(int, '__doc__')",
        "lambda: 42",
        "[x for x in range(10)]",
        "{'a': 1}",
        "(lambda x: x)(5)",
        "print('hello')",
        "globals()",
        "locals()",
        "vars()",
        "breakpoint()",
    ]

    for payload in dangerous_payloads:
        with pytest.raises(ValueError) as exc_info:
            safe_calculate(payload)
        msg = str(exc_info.value).lower()
        assert "unsupported" in msg or "disallowed" in msg or "invalid" in msg or "syntax" in msg


def test_calculator_syntax_and_boundary_errors() -> None:
    """Verify malformed and boundary inputs fail cleanly with ValueError."""
    syntax_errors = [
        "",
        "   ",
        "1 + ",
        "+ * 2",
        "((1 + 2)",
        "1 + 2))",
        "2 ** 10000000",  # excessively large exponent power limit
        "invalid_identifier",
        "100 / 0",  # ZeroDivisionError
    ]

    for err in syntax_errors:
        with pytest.raises((ValueError, ZeroDivisionError)):
            safe_calculate(err)


def test_sql_destructive_keywords_blocked() -> None:
    """Verify non-SELECT and destructive statements are strictly rejected."""
    destructive_queries = [
        "DROP TABLE users",
        "DELETE FROM orders WHERE id = 1",
        "UPDATE products SET price = 0",
        "INSERT INTO users (name) VALUES ('hacker')",
        "ALTER TABLE users ADD COLUMN is_admin INT",
        "TRUNCATE TABLE logs",
        "CREATE TABLE backdoor (id INT)",
        "ATTACH DATABASE '/tmp/pwn.db' AS pwn",
        "PRAGMA table_info(users)",
        "VACUUM",
    ]

    for query in destructive_queries:
        with pytest.raises(ValueError) as exc_info:
            validate_sql_query(query=query)
        msg = str(exc_info.value).lower()
        assert "only select statements" in msg or "prohibited" in msg


def test_sql_stacked_queries_and_comments_blocked() -> None:
    """Verify multiple statements and suspicious comment payloads are blocked."""
    stacked_queries = [
        "SELECT 1; DROP TABLE users;",
        "SELECT 1; SELECT 2;",
        "SELECT * FROM products; DELETE FROM products;",
        "SELECT 1 /* comment */",
        "SELECT 1 -- comment",
    ]

    for query in stacked_queries:
        with pytest.raises(ValueError) as exc_info:
            validate_sql_query(query=query)
        msg = str(exc_info.value).lower()
        assert "multiple" in msg or "comment" in msg or "prohibited" in msg


def test_file_reader_path_traversal_and_symlinks_blocked(tmp_path: Path) -> None:
    """Verify sandboxed file reader rejects path traversals, escapes, and symlink loops."""
    sandbox_dir = tmp_path / "sandbox"
    sandbox_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()

    secret_file = outside_dir / "secret.txt"
    secret_file.write_text("SUPER_SECRET_TOKEN", encoding="utf-8")

    valid_file = sandbox_dir / "valid.txt"
    valid_file.write_text("Public knowledge", encoding="utf-8")

    # 1. Path traversal attempts
    traversal_attempts = [
        "../outside/secret.txt",
        "../../secret.txt",
        "sub/../../outside/secret.txt",
        str(secret_file.resolve()),
        "/etc/passwd",
    ]
    for attempt in traversal_attempts:
        with pytest.raises(PermissionError) as exc_info:
            read_sandboxed_file(attempt, base_dir=sandbox_dir)
        assert "outside allowed base directory" in str(exc_info.value).lower()

    # 2. Symlink escape attempt (symlink in sandbox pointing outside)
    symlink_outside = sandbox_dir / "symlink_escape.txt"
    symlink_outside.symlink_to(secret_file)

    with pytest.raises(PermissionError) as exc_info:
        read_sandboxed_file("symlink_escape.txt", base_dir=sandbox_dir)
    assert "outside allowed base directory" in str(exc_info.value).lower()

    # 3. Reading a directory
    sub_dir = sandbox_dir / "subfolder"
    sub_dir.mkdir()
    with pytest.raises(ValueError) as exc_dir:
        read_sandboxed_file("subfolder", base_dir=sandbox_dir)
    assert "not a regular file" in str(exc_dir.value).lower()

    # 4. File exceeding max size limit
    large_file = sandbox_dir / "large.txt"
    large_file.write_bytes(b"A" * 1000)
    with pytest.raises(ValueError) as exc_size:
        read_sandboxed_file("large.txt", base_dir=sandbox_dir, max_bytes=500)
    assert "exceeds maximum size limit" in str(exc_size.value).lower()

    # 5. Non-existent file
    with pytest.raises(FileNotFoundError):
        read_sandboxed_file("ghost.txt", base_dir=sandbox_dir)


def test_http_fetch_ssrf_blocked() -> None:
    """Verify HTTP SSRF validator rejects private IPs, loopbacks, and cloud metadata endpoints."""
    blocked_urls = [
        # Loopback
        "http://127.0.0.1/admin",
        "http://127.0.0.2:8080/",
        "http://localhost/",
        "http://localhost:3000/api",
        # RFC1918 Private IPv4
        "http://10.0.0.1/internal",
        "http://10.255.255.254/status",
        "http://172.16.0.1/metrics",
        "http://172.31.255.255/secret",
        "http://192.168.1.1/router",
        "http://192.168.0.100:9000/db",
        # Cloud Metadata (AWS/GCP/Azure link-local)
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.1.1/",
        # Non-HTTP Schemes
        "file:///etc/passwd",
        "ftp://example.com/file",
        "gopher://example.com/",
        "javascript:alert(1)",
        # Malformed
        "http:///missing-host",
    ]

    for url in blocked_urls:
        with pytest.raises(ValueError) as exc_info:
            validate_url_ssrf(url)
        assert (
            "ssrf" in str(exc_info.value).lower()
            or "disallowed" in str(exc_info.value).lower()
            or "valid hostname" in str(exc_info.value).lower()
        )
