"""Sandboxed file reader tool with path traversal and symlink escape defenses."""

from pathlib import Path

from agentkit.config import get_settings
from agentkit.tools.registry import tool

DEFAULT_MAX_BYTES = 500_000  # 500 KB default size ceiling


def read_sandboxed_file(
    file_path: str,
    base_dir: Path | str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> str:
    """Read file content with strict sandboxing and path traversal validation.

    Args:
        file_path: Path to target file (relative or absolute).
        base_dir: Sandbox directory boundary. Defaults to Settings.FILE_TOOL_BASE_DIR.
        max_bytes: Maximum allowed file size in bytes.

    Returns:
        The text content of the file as UTF-8 string.

    Raises:
        PermissionError: If path attempts to escape sandbox boundary via traversal or symlinks.
        FileNotFoundError: If the target file does not exist.
        ValueError: If target is not a regular file, exceeds max size, or is not valid UTF-8.
    """
    settings_base = base_dir if base_dir is not None else get_settings().FILE_TOOL_BASE_DIR
    base = Path(settings_base).resolve()

    candidate = Path(file_path)
    # If relative, anchor to base; if absolute, start from candidate
    raw_target = candidate if candidate.is_absolute() else (base / candidate)

    # Fully resolve all symlinks and relative segments
    target = raw_target.resolve()

    # Enforce strict sandbox containment
    if not target.is_relative_to(base):
        raise PermissionError(
            f"Access denied: path '{file_path}' resolves outside allowed base directory."
        )

    if not target.exists():
        raise FileNotFoundError(f"File not found: '{file_path}'")

    if not target.is_file():
        raise ValueError(f"Target is not a regular file: '{file_path}'")

    # The byte cap is enforced on the bytes actually read, not on a prior st_size: a file
    # grown between a stat check and a second open would otherwise be read in full (the
    # check-then-read race). One bounded read of max_bytes + 1 closes that window while
    # making oversize still fail — and an exact-cap file stays fully readable.
    with target.open("rb") as handle:
        data = handle.read(max_bytes + 1)

    if len(data) > max_bytes:
        raise ValueError(f"File '{file_path}' exceeds maximum size limit of {max_bytes} bytes.")

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"File '{file_path}' is not a valid UTF-8 text file.") from exc


@tool
def read_file(file_path: str) -> str:
    """Read the contents of a text file within the sandboxed directory.

    All paths are validated to prevent directory traversal and symlink escapes.
    Only valid UTF-8 text files under the configured sandbox are readable.
    """
    return read_sandboxed_file(file_path)
