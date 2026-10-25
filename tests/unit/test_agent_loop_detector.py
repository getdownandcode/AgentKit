"""Unit tests for duplicate tool call and infinite loop detection."""

import pytest

from agentkit.core.agent import Agent, AgentConfig
from agentkit.core.errors import DuplicateToolCallLoopError
from agentkit.core.types import Role, RunStatus
from agentkit.llm.base import LLMResponse, Message
from agentkit.llm.fake import FakeLLMClient
from agentkit.memory.base import InMemoryMemoryStore
from agentkit.tools.models import ToolCall
from agentkit.tools.registry import ToolRegistry, tool


def _assert_history_is_provider_valid(messages: list[Message]) -> None:
    """Assert every assistant tool-call id has a matching tool message.

    Mirrors the invariant enforced by OpenAI and Gemini: an assistant turn requesting
    tool calls must be followed by a tool message for each call id before any other
    turn appears.
    """
    pending: set[str] = set()
    for msg in messages:
        if msg.role == Role.TOOL:
            assert msg.tool_call_id is not None
            pending.discard(msg.tool_call_id)
        else:
            assert not pending, f"Unanswered tool calls before {msg.role} message: {pending}"
            if msg.role == Role.ASSISTANT:
                pending = {call.id for call in msg.tool_calls}
    assert not pending, f"Unanswered tool calls at end of history: {pending}"


@pytest.mark.asyncio
async def test_duplicate_tool_call_halts_with_failure_status() -> None:
    registry = ToolRegistry()

    @tool(registry=registry)
    def search_query(q: str) -> str:
        """Search query."""
        return f"result for {q}"

    # 3 identical consecutive calls with q="python"
    identical_call = ToolCall(id="c1", name="search_query", arguments={"q": "python"})
    llm = FakeLLMClient(
        responses=[
            LLMResponse(text="Searching...", tool_calls=[identical_call]),
            LLMResponse(text="Searching again...", tool_calls=[identical_call]),
            LLMResponse(text="Searching again 2...", tool_calls=[identical_call]),
        ]
    )

    agent = Agent(
        llm=llm,
        registry=registry,
        config=AgentConfig(max_steps=10, max_consecutive_duplicate_tool_calls=3),
    )
    result = await agent.run(goal="Infinite loop")

    assert result.status == RunStatus.FAILED
    assert "duplicate tool call loop" in (result.failure_reason or "").lower()
    assert "search_query" in (result.failure_reason or "")
    assert result.final_answer is None


@pytest.mark.asyncio
async def test_duplicate_tool_call_raises_when_requested() -> None:
    registry = ToolRegistry()

    @tool(registry=registry)
    def repeat_me(val: int) -> int:
        """Repeat."""
        return val

    identical_call = ToolCall(id="c1", name="repeat_me", arguments={"val": 42})
    llm = FakeLLMClient(
        responses=[
            LLMResponse(text="Repeat 1", tool_calls=[identical_call]),
            LLMResponse(text="Repeat 2", tool_calls=[identical_call]),
        ]
    )

    agent = Agent(
        llm=llm,
        registry=registry,
        config=AgentConfig(max_steps=5, max_consecutive_duplicate_tool_calls=2),
    )

    with pytest.raises(DuplicateToolCallLoopError) as exc_info:
        await agent.run(goal="Test raise", raise_on_failure=True)

    assert exc_info.value.tool_name == "repeat_me"
    assert exc_info.value.count == 2
    assert exc_info.value.code == "DUPLICATE_TOOL_CALL_LOOP"


@pytest.mark.asyncio
async def test_different_arguments_do_not_trigger_detector() -> None:
    registry = ToolRegistry()

    @tool(registry=registry)
    def search_query(q: str) -> str:
        """Search query."""
        return f"result for {q}"

    llm = FakeLLMClient(
        responses=[
            LLMResponse(
                text="Search 1",
                tool_calls=[ToolCall(id="c1", name="search_query", arguments={"q": "step1"})],
            ),
            LLMResponse(
                text="Search 2",
                tool_calls=[ToolCall(id="c2", name="search_query", arguments={"q": "step2"})],
            ),
            LLMResponse(text="Final answer: found everything!"),
        ]
    )

    agent = Agent(
        llm=llm,
        registry=registry,
        config=AgentConfig(max_steps=5, max_consecutive_duplicate_tool_calls=2),
    )
    result = await agent.run(goal="Search steps")

    assert result.status == RunStatus.SUCCEEDED
    assert result.final_answer == "Final answer: found everything!"


@pytest.mark.asyncio
async def test_alternating_tools_do_not_trigger_detector() -> None:
    registry = ToolRegistry()

    @tool(registry=registry)
    def tool_a(x: int) -> int:
        """Tool A."""
        return x + 1

    @tool(registry=registry)
    def tool_b(x: int) -> int:
        """Tool B."""
        return x * 2

    # Alternate tools with distinct arguments. Alternating the *same* call is now a loop:
    # the detector counts repeats inside a sliding window, not only consecutive ones
    # (AUDIT-15), so each signature here must differ.
    llm = FakeLLMClient(
        responses=[
            LLMResponse(
                text="A 1",
                tool_calls=[ToolCall(id="c1", name="tool_a", arguments={"x": 1})],
            ),
            LLMResponse(
                text="B 1",
                tool_calls=[ToolCall(id="c2", name="tool_b", arguments={"x": 2})],
            ),
            LLMResponse(
                text="A 2",
                tool_calls=[ToolCall(id="c3", name="tool_a", arguments={"x": 3})],
            ),
            LLMResponse(text="Done"),
        ]
    )

    agent = Agent(
        llm=llm,
        registry=registry,
        config=AgentConfig(max_steps=10, max_consecutive_duplicate_tool_calls=2),
    )
    result = await agent.run(goal="Alternating")

    assert result.status == RunStatus.SUCCEEDED
    assert result.final_answer == "Done"


@pytest.mark.asyncio
async def test_duplicate_abort_persists_provider_valid_session_history() -> None:
    """The unexecuted duplicate call must still get a tool message in the session."""
    registry = ToolRegistry()

    @tool(registry=registry)
    def repeat_session(val: int) -> int:
        """Repeat."""
        return val

    first_call = ToolCall(id="c1", name="repeat_session", arguments={"val": 42})
    duplicate_call = ToolCall(id="c2", name="repeat_session", arguments={"val": 42})
    llm = FakeLLMClient(
        responses=[
            LLMResponse(text="Repeat 1", tool_calls=[first_call]),
            LLMResponse(text="Repeat 2", tool_calls=[duplicate_call]),
        ]
    )
    memory = InMemoryMemoryStore()
    agent = Agent(
        llm=llm,
        registry=registry,
        memory=memory,
        config=AgentConfig(max_steps=5, max_consecutive_duplicate_tool_calls=2),
    )

    result = await agent.run(goal="Loop with session", session_id="sess-loop")

    assert result.status == RunStatus.FAILED

    messages = await memory.get_messages("sess-loop")
    _assert_history_is_provider_valid(messages)

    tool_message = next(m for m in messages if m.tool_call_id == "c2")
    assert "aborted" in tool_message.content.lower()


@pytest.mark.asyncio
async def test_duplicate_abort_mid_batch_answers_remaining_calls() -> None:
    """Aborting partway through a multi-call turn must not drop the later call ids."""
    registry = ToolRegistry()

    @tool(registry=registry)
    def echo_tool(v: int) -> int:
        """Echo value."""
        return v

    llm = FakeLLMClient(
        responses=[
            LLMResponse(
                text="Setup call",
                tool_calls=[ToolCall(id="c_setup", name="echo_tool", arguments={"v": 1})],
            ),
            LLMResponse(
                text="Batch with a repeat",
                tool_calls=[
                    ToolCall(id="c_batch_1", name="echo_tool", arguments={"v": 2}),
                    ToolCall(id="c_batch_2", name="echo_tool", arguments={"v": 2}),
                ],
            ),
        ]
    )
    memory = InMemoryMemoryStore()
    agent = Agent(
        llm=llm,
        registry=registry,
        memory=memory,
        config=AgentConfig(max_steps=5, max_consecutive_duplicate_tool_calls=2),
    )

    result = await agent.run(goal="Loop mid batch", session_id="sess-mid-batch")

    assert result.status == RunStatus.FAILED

    messages = await memory.get_messages("sess-mid-batch")
    _assert_history_is_provider_valid(messages)

    executed = next(m for m in messages if m.tool_call_id == "c_batch_1")
    assert executed.content == "2"

    interrupted = next(m for m in messages if m.tool_call_id == "c_batch_2")
    assert "aborted" in interrupted.content.lower()
