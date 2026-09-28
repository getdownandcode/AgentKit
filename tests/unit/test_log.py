import io
import json
import logging
from unittest.mock import MagicMock

import pytest

from agentkit.core.agent import Agent
from agentkit.core.log import (
    JSONFormatter,
    clear_log_context,
    get_log_context,
    log_context,
    set_current_run_id,
    set_current_step_no,
)
from agentkit.core.trace import InMemoryTraceSink
from agentkit.llm.base import LLMResponse
from agentkit.llm.fake import FakeLLMClient
from agentkit.tools.models import ToolCall
from agentkit.tools.registry import ToolRegistry, tool


def test_json_formatter_default_output() -> None:
    clear_log_context()
    formatter = JSONFormatter()
    logger = logging.getLogger("test.logger")
    record = logger.makeRecord(
        name="test.logger",
        level=logging.INFO,
        fn="test_file.py",
        lno=42,
        msg="Hello %s",
        args=("world",),
        exc_info=None,
    )

    formatted = formatter.format(record)
    data = json.loads(formatted)

    assert data["level"] == "INFO"
    assert data["logger"] == "test.logger"
    assert data["message"] == "Hello world"
    assert "timestamp" in data
    assert data.get("run_id") is None
    assert data.get("step_no") is None


def test_json_formatter_with_context() -> None:
    clear_log_context()
    formatter = JSONFormatter()
    logger = logging.getLogger("test.context")

    with log_context(run_id="run_999", step_no=3):
        assert get_log_context() == ("run_999", 3)
        record = logger.makeRecord(
            name="test.context",
            level=logging.WARNING,
            fn="test_file.py",
            lno=10,
            msg="Warning message",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        data = json.loads(formatted)
        assert data["run_id"] == "run_999"
        assert data["step_no"] == 3
        assert data["level"] == "WARNING"
        assert data["message"] == "Warning message"

    # Context restored after exit
    assert get_log_context() == (None, None)
    record2 = logger.makeRecord(
        name="test.context",
        level=logging.INFO,
        fn="test_file.py",
        lno=20,
        msg="After context",
        args=(),
        exc_info=None,
    )
    data2 = json.loads(formatter.format(record2))
    assert data2.get("run_id") is None
    assert data2.get("step_no") is None


def test_nested_log_context() -> None:
    clear_log_context()
    with log_context(run_id="run_parent", step_no=1):
        assert get_log_context() == ("run_parent", 1)
        with log_context(step_no=2):
            assert get_log_context() == ("run_parent", 2)
        assert get_log_context() == ("run_parent", 1)
    assert get_log_context() == (None, None)


def test_set_log_context_functions() -> None:
    clear_log_context()
    set_current_run_id("manual_run")
    set_current_step_no(5)
    assert get_log_context() == ("manual_run", 5)
    clear_log_context()
    assert get_log_context() == (None, None)


def test_json_formatter_with_exception() -> None:
    formatter = JSONFormatter()
    logger = logging.getLogger("test.exc")

    try:
        raise ValueError("Something broke")
    except ValueError:
        import sys

        exc_info = sys.exc_info()
        record = logger.makeRecord(
            name="test.exc",
            level=logging.ERROR,
            fn="test_file.py",
            lno=30,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )
        data = json.loads(formatter.format(record))
        assert data["level"] == "ERROR"
        assert "ValueError: Something broke" in data["exc_info"]


@pytest.mark.asyncio
async def test_agent_emits_logs_with_context() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())

    agent_logger = logging.getLogger("agentkit.core.agent")
    agent_logger.addHandler(handler)
    agent_logger.setLevel(logging.INFO)

    registry = ToolRegistry()

    @tool(registry=registry)
    def dummy_action() -> str:
        """Dummy."""
        return "action done"

    llm = FakeLLMClient(
        responses=[
            LLMResponse(
                text="Calling tool",
                tool_calls=[ToolCall(id="c1", name="dummy_action", arguments={})],
            ),
            LLMResponse(text="Final answer"),
        ]
    )

    sink = InMemoryTraceSink()
    agent = Agent(llm=llm, registry=registry, trace=sink)

    try:
        result = await agent.run(goal="Test context logging")
        lines = [line for line in stream.getvalue().strip().split("\n") if line]
        assert len(lines) > 0

        parsed_lines = [json.loads(line) for line in lines]
        # At least one log record should have run_id matching result.run_id
        run_ids = [p.get("run_id") for p in parsed_lines]
        assert result.run_id in run_ids
    finally:
        agent_logger.removeHandler(handler)
