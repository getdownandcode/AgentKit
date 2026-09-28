"""Unit tests for neutral LLM client interface and message models."""

import pytest

from agentkit.core.types import Role
from agentkit.llm.base import LLMClient, LLMResponse, Message, TokenUsage
from agentkit.tools.models import ToolCall, ToolSchema


def test_token_usage_model() -> None:
    """Verify TokenUsage calculations."""
    usage = TokenUsage(input_tokens=120, output_tokens=45)
    assert usage.input_tokens == 120
    assert usage.output_tokens == 45
    assert usage.total_tokens == 165


def test_message_factories_and_attributes() -> None:
    """Verify helper factories for creating system, user, assistant, and tool messages."""
    msg_sys = Message.system("You are a helpful assistant.")
    assert msg_sys.role == Role.SYSTEM
    assert msg_sys.content == "You are a helpful assistant."
    assert msg_sys.tool_calls == []
    assert msg_sys.tool_call_id is None

    msg_user = Message.user("What is the weather?")
    assert msg_user.role == Role.USER
    assert msg_user.content == "What is the weather?"

    call = ToolCall(id="call_99", name="get_weather", arguments={"city": "Tokyo"})
    msg_asst = Message.assistant(content="", tool_calls=[call])
    assert msg_asst.role == Role.ASSISTANT
    assert len(msg_asst.tool_calls) == 1
    assert msg_asst.tool_calls[0].name == "get_weather"

    msg_tool = Message.tool_result(tool_call_id="call_99", content="Sunny, 22C")
    assert msg_tool.role == Role.TOOL
    assert msg_tool.tool_call_id == "call_99"
    assert msg_tool.content == "Sunny, 22C"


def test_llm_response_model() -> None:
    """Verify LLMResponse structure and defaults."""
    call = ToolCall(id="call_1", name="calc", arguments={"expr": "1+1"})
    usage = TokenUsage(input_tokens=50, output_tokens=20)
    resp = LLMResponse(
        text="Executing calculation...",
        tool_calls=[call],
        usage=usage,
        raw_metadata={"model": "test-model"},
    )
    assert resp.text == "Executing calculation..."
    assert len(resp.tool_calls) == 1
    assert resp.usage.total_tokens == 70
    assert resp.raw_metadata["model"] == "test-model"


def test_cannot_instantiate_abstract_llm_client() -> None:
    """Verify LLMClient ABC cannot be instantiated directly."""
    with pytest.raises(TypeError):
        LLMClient()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_concrete_llm_client_subclass() -> None:
    """Verify a concrete subclass of LLMClient implements chat."""

    class DummyClient(LLMClient):
        async def chat(
            self,
            messages: list[Message],
            tools: list[ToolSchema] | None = None,
        ) -> LLMResponse:
            tool_count = len(tools or [])
            return LLMResponse(text=f"Echo {len(messages)} messages with {tool_count} tools")

    client = DummyClient()
    resp = await client.chat([Message.user("Hello")])
    assert resp.text == "Echo 1 messages with 0 tools"
