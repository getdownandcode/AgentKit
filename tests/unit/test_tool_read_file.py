"""Unit and security tests for sandboxed file reader tool."""

from pathlib import Path

import pytest

from agentkit.tools.builtin.read_file import read_file, read_sandboxed_file
from agentkit.tools.models import ToolCall
from agentkit.tools.registry import ToolRegistry


def test_read_file_success(tmp_path: Path) -> None:
    doc = tmp_path / "hello.txt"
    doc.write_text("Hello, AgentKit!", encoding="utf-8")

    content = read_sandboxed_file("hello.txt", base_dir=tmp_path)
    assert content == "Hello, AgentKit!"


def test_read_file_nested_subdir(tmp_path: Path) -> None:
    nested = tmp_path / "sub" / "folder"
    nested.mkdir(parents=True)
    doc = nested / "data.csv"
    doc.write_text("a,b,c\n1,2,3", encoding="utf-8")

    content = read_sandboxed_file("sub/folder/data.csv", base_dir=tmp_path)
    assert content == "a,b,c\n1,2,3"


def test_path_traversal_escapes_rejected(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret_data", encoding="utf-8")

    with pytest.raises(PermissionError, match="outside allowed base directory"):
        read_sandboxed_file("../secret.txt", base_dir=sandbox)

    with pytest.raises(PermissionError, match="outside allowed base directory"):
        read_sandboxed_file("/etc/passwd", base_dir=sandbox)


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside data", encoding="utf-8")

    # Create symlink inside sandbox pointing outside
    symlink = sandbox / "link_outside.txt"
    symlink.symlink_to(outside_file)

    with pytest.raises(PermissionError, match="outside allowed base directory"):
        read_sandboxed_file("link_outside.txt", base_dir=sandbox)


def test_symlink_inside_allowed(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    target_file = sandbox / "actual.txt"
    target_file.write_text("inside content", encoding="utf-8")

    link = sandbox / "link_inside.txt"
    link.symlink_to(target_file)

    content = read_sandboxed_file("link_inside.txt", base_dir=sandbox)
    assert content == "inside content"


def test_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="File not found"):
        read_sandboxed_file("nonexistent.txt", base_dir=tmp_path)


def test_directory_read_rejected(tmp_path: Path) -> None:
    sub = tmp_path / "somedir"
    sub.mkdir()

    with pytest.raises((ValueError, IsADirectoryError), match="not a regular file"):
        read_sandboxed_file("somedir", base_dir=tmp_path)


def test_max_bytes_limit_enforced(tmp_path: Path) -> None:
    doc = tmp_path / "large.txt"
    doc.write_text("A" * 500, encoding="utf-8")

    with pytest.raises(ValueError, match="exceeds maximum size limit"):
        read_sandboxed_file("large.txt", base_dir=tmp_path, max_bytes=100)


def test_binary_file_rejected(tmp_path: Path) -> None:
    doc = tmp_path / "binary.bin"
    doc.write_bytes(b"\x80\x81\xff\xfe\x00\x01")

    with pytest.raises(ValueError, match="not a valid UTF-8 text file"):
        read_sandboxed_file("binary.bin", base_dir=tmp_path)


@pytest.mark.asyncio
async def test_file_reader_tool_registry_integration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentkit.config import get_settings

    # Monkeypatch settings base dir to tmp_path
    monkeypatch.setattr(get_settings(), "FILE_TOOL_BASE_DIR", str(tmp_path))

    doc = tmp_path / "notes.txt"
    doc.write_text("Sample notes", encoding="utf-8")

    registry = ToolRegistry()
    registry.register(read_file)

    call = ToolCall(id="call_1", name="read_file", arguments={"file_path": "notes.txt"})
    result = await registry.execute(call)

    assert result.ok is True
    assert result.output == "Sample notes"

    bad_call = ToolCall(id="call_2", name="read_file", arguments={"file_path": "../escape.txt"})
    bad_result = await registry.execute(bad_call)

    assert bad_result.ok is False
    assert "outside allowed base directory" in (bad_result.error or "")
