"""Unit tests for scripted FakeLLMClient."""

import pytest

from agentkit.llm.base import LLMResponse, Message, TokenUsage
from agentkit.llm.fake import FakeLLMClient
from agentkit.tools.models import ToolSchema


@pytest.mark.asyncio
async def test_fake_llm_sequential_responses() -> None:
    """Verify FakeLLMClient returns scripted responses in FIFO order."""
    client = FakeLLMClient()
    client.queue_text("First response")
    client.queue_tool_call("calculator", {"expression": "2 + 2"}, call_id="c_1")
    client.queue_text("Final answer: 4")

    # Turn 1
    resp1 = await client.chat([Message.user("Hello")])
    assert resp1.text == "First response"
    assert resp1.tool_calls == []
    assert client.call_count == 1

    # Turn 2
    resp2 = await client.chat([Message.user("Calculate 2+2")])
    assert len(resp2.tool_calls) == 1
    assert resp2.tool_calls[0].name == "calculator"
    assert resp2.tool_calls[0].arguments == {"expression": "2 + 2"}
    assert resp2.tool_calls[0].id == "c_1"
    assert client.call_count == 2

    # Turn 3
    resp3 = await client.chat([Message.tool_result("c_1", "4")])
    assert resp3.text == "Final answer: 4"
    assert client.call_count == 3


@pytest.mark.asyncio
async def test_fake_llm_records_history_and_tools() -> None:
    """Verify FakeLLMClient records call history and tools provided."""
    client = FakeLLMClient()
    client.queue_text("OK")

    dummy_tool = ToolSchema(
        name="test_tool",
        description="A test tool",
        parameters={"type": "object", "properties": {}},
    )

    msg = Message.user("Test message")
    await client.chat([msg], tools=[dummy_tool])

    assert len(client.history) == 1
    assert client.history[0][0].content == "Test message"
    assert len(client.tools_history) == 1
    assert client.tools_history[0] is not None
    assert client.tools_history[0][0].name == "test_tool"


@pytest.mark.asyncio
async def test_fake_llm_queue_exhaustion() -> None:
    """Verify FakeLLMClient raises RuntimeError when response queue is empty."""
    client = FakeLLMClient()
    with pytest.raises(RuntimeError, match="queue exhausted"):
        await client.chat([Message.user("Hi")])


@pytest.mark.asyncio
async def test_fake_llm_fallback_default_response() -> None:
    """Verify default response is used if queue is exhausted."""
    fallback = LLMResponse(
        text="Default fallback", usage=TokenUsage(input_tokens=5, output_tokens=5)
    )
    client = FakeLLMClient(default_response=fallback)

    resp1 = await client.chat([Message.user("1")])
    resp2 = await client.chat([Message.user("2")])
    assert resp1.text == "Default fallback"
    assert resp2.text == "Default fallback"
    assert client.call_count == 2
