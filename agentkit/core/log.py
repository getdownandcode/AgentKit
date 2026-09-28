"""Structured JSON logging and execution context propagation."""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# Standard LogRecord attribute names to ignore when extracting extra attributes
_STANDARD_LOG_RECORD_ATTRS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}

_current_run_id: ContextVar[str | None] = ContextVar("current_run_id", default=None)
_current_step_no: ContextVar[int | None] = ContextVar("current_step_no", default=None)


def get_log_context() -> tuple[str | None, int | None]:
    """Return the current context (run_id, step_no)."""
    return _current_run_id.get(), _current_step_no.get()


def set_current_run_id(run_id: str | None) -> None:
    """Set the contextual run_id."""
    _current_run_id.set(run_id)


def set_current_step_no(step_no: int | None) -> None:
    """Set the contextual step_no."""
    _current_step_no.set(step_no)


def clear_log_context() -> None:
    """Clear all contextual identifiers."""
    _current_run_id.set(None)
    _current_step_no.set(None)


@contextmanager
def log_context(
    run_id: str | None = None,
    step_no: int | None = None,
) -> Iterator[None]:
    """Context manager setting contextual identifiers and restoring previous values on exit."""
    tokens: list[tuple[ContextVar[Any], Any]] = []
    if run_id is not None:
        tokens.append((_current_run_id, _current_run_id.set(run_id)))
    if step_no is not None:
        tokens.append((_current_step_no, _current_step_no.set(step_no)))

    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


class JSONFormatter(logging.Formatter):
    """Logging formatter emitting machine-readable structured JSON lines."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record as a single-line JSON string."""
        message = record.getMessage()

        run_id = getattr(record, "run_id", None) or _current_run_id.get()
        step_no = getattr(record, "step_no", None) or _current_step_no.get()

        log_data: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "run_id": run_id,
            "step_no": step_no,
        }

        if record.exc_info:
            log_data["exc_info"] = self.formatException(record.exc_info)

        if record.stack_info:
            log_data["stack_info"] = self.formatStack(record.stack_info)

        # Include custom extra kwargs passed to logger
        for key, val in record.__dict__.items():
            if key not in _STANDARD_LOG_RECORD_ATTRS and key not in log_data:
                log_data[key] = val

        return json.dumps(log_data, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logger with JSONFormatter stream handler."""
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing stream handlers to avoid duplicates
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root_logger.addHandler(handler)
