from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config
import pytest


def test_alembic_migrations_lifecycle(tmp_path: Path) -> None:
    db_file = tmp_path / "test_migration.db"
    db_url = f"sqlite:///{db_file}"

    ini_path = Path("alembic.ini")
    assert ini_path.exists(), "alembic.ini must exist in project root"

    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)

    # 1. Upgrade to head
    command.upgrade(cfg, "head")

    # Verify tables and indexes exist using sqlite3 connection
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row[0] for row in cursor.fetchall()}
    assert "runs" in tables
    assert "steps" in tables
    assert "alembic_version" in tables

    # Check columns in runs
    cursor.execute("PRAGMA table_info(runs);")
    run_cols = {row[1] for row in cursor.fetchall()}
    assert {"id", "session_id", "goal", "status", "final_answer", "failure_reason", "total_input_tokens", "total_output_tokens", "created_at"}.issubset(run_cols)

    # Check columns in steps
    cursor.execute("PRAGMA table_info(steps);")
    step_cols = {row[1] for row in cursor.fetchall()}
    assert {"id", "run_id", "step_no", "tool_name", "args", "result", "error", "latency_ms", "input_tokens", "output_tokens", "created_at"}.issubset(step_cols)

    # Check index on steps(run_id, step_no)
    cursor.execute("PRAGMA index_list(steps);")
    step_indices = {row[1] for row in cursor.fetchall()}
    assert any("run_id" in idx for idx in step_indices)

    # 2. Downgrade to base
    command.downgrade(cfg, "base")

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables_after = {row[0] for row in cursor.fetchall()}
    assert "runs" not in tables_after
    assert "steps" not in tables_after

    conn.close()
