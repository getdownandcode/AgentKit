"""Unit tests for Gemini LLM adapter with mocked Google GenAI client."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentkit.core.types import Role
from agentkit.llm.base import Message
from agentkit.llm.gemini import GeminiClient
from agentkit.tools.models import ToolCall, ToolSchema


@pytest.fixture
def mock_genai_client() -> MagicMock:
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_gemini_text_chat_translation(mock_genai_client: MagicMock) -> None:
    """Verify standard text prompt translation and response extraction."""
    # Mock Gemini API response
    mock_candidate = MagicMock()
    mock_part = MagicMock()
    mock_part.text = "Hello there!"
    mock_part.function_call = None
    mock_candidate.content.parts = [mock_part]

    mock_response = MagicMock()
    mock_response.candidates = [mock_candidate]
    mock_response.text = "Hello there!"
    mock_response.usage_metadata.prompt_token_count = 12
    mock_response.usage_metadata.candidates_token_count = 8
    mock_response.model_dump.return_value = {"candidates": []}

    mock_genai_client.aio.models.generate_content.return_value = mock_response

    adapter = GeminiClient(api_key="fake-key", model="gemini-2.5-flash", client=mock_genai_client)
    messages = [
        Message.system("Act as an expert assistant."),
        Message.user("Hello!"),
    ]

    response = await adapter.chat(messages)

    assert response.text == "Hello there!"
    assert response.tool_calls == []
    assert response.usage.input_tokens == 12
    assert response.usage.output_tokens == 8
    assert response.usage.total_tokens == 20
    assert mock_genai_client.aio.models.generate_content.called


@pytest.mark.asyncio
async def test_gemini_tool_call_translation(mock_genai_client: MagicMock) -> None:
    """Verify function calling translation and tool call extraction."""
    mock_fc = MagicMock()
    mock_fc.name = "calculator"
    mock_fc.args = {"expression": "100 / 4"}

    mock_part = MagicMock()
    mock_part.text = None
    mock_part.function_call = mock_fc
    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]

    mock_response = MagicMock()
    mock_response.candidates = [mock_candidate]
    mock_response.text = ""
    mock_response.usage_metadata.prompt_token_count = 40
    mock_response.usage_metadata.candidates_token_count = 15
    mock_response.model_dump.return_value = {}

    mock_genai_client.aio.models.generate_content.return_value = mock_response

    adapter = GeminiClient(api_key="fake-key", client=mock_genai_client)

    tool_schema = ToolSchema(
        name="calculator",
        description="Evaluate arithmetic",
        parameters={
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
    )

    messages = [Message.user("Calculate 100/4")]
    response = await adapter.chat(messages, tools=[tool_schema])

    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert call.name == "calculator"
    assert call.arguments == {"expression": "100 / 4"}
    assert call.id != ""


@pytest.mark.asyncio
async def test_gemini_multi_turn_history_translation(mock_genai_client: MagicMock) -> None:
    """Verify tool results and multi-turn message history conversion."""
    mock_candidate = MagicMock()
    mock_part = MagicMock()
    mock_part.text = "Result is 25."
    mock_part.function_call = None
    mock_candidate.content.parts = [mock_part]

    mock_response = MagicMock()
    mock_response.candidates = [mock_candidate]
    mock_response.text = "Result is 25."
    mock_response.usage_metadata.prompt_token_count = 60
    mock_response.usage_metadata.candidates_token_count = 5
    mock_response.model_dump.return_value = {}

    mock_genai_client.aio.models.generate_content.return_value = mock_response

    adapter = GeminiClient(api_key="fake-key", client=mock_genai_client)

    prev_call = ToolCall(id="c_1", name="calculator", arguments={"expression": "100 / 4"})
    messages = [
        Message.user("Calculate 100/4"),
        Message.assistant(content="", tool_calls=[prev_call]),
        Message(role=Role.TOOL, content="25", tool_call_id="c_1"),
    ]

    response = await adapter.chat(messages)
    assert response.text == "Result is 25."
    assert mock_genai_client.aio.models.generate_content.called
    _, kwargs = mock_genai_client.aio.models.generate_content.call_args
    contents = kwargs["contents"]
    assert contents[2].role == "user"


@pytest.mark.asyncio
async def test_gemini_thought_signature_capture_and_forward(mock_genai_client: MagicMock) -> None:
    """Verify thought_signature from candidate is captured in ToolCall and forwarded back."""
    mock_fc = MagicMock()
    mock_fc.name = "calculator"
    mock_fc.args = {"expression": "2 + 2"}

    mock_part = MagicMock()
    mock_part.text = None
    mock_part.function_call = mock_fc
    mock_part.thought_signature = b"opaque_thought_sig_xyz"

    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]

    mock_response = MagicMock()
    mock_response.candidates = [mock_candidate]
    mock_response.text = ""
    mock_response.usage_metadata.prompt_token_count = 10
    mock_response.usage_metadata.candidates_token_count = 5
    mock_response.model_dump.return_value = {}

    mock_genai_client.aio.models.generate_content.return_value = mock_response

    adapter = GeminiClient(api_key="fake-key", client=mock_genai_client)

    # 1. Turn 1: model returns tool call with thought_signature
    messages = [Message.user("What is 2+2?")]
    turn1_resp = await adapter.chat(messages)

    assert len(turn1_resp.tool_calls) == 1
    tc = turn1_resp.tool_calls[0]
    assert tc.name == "calculator"
    assert tc.thought_signature == b"opaque_thought_sig_xyz"

    # 2. Turn 2: send assistant tool call with thought_signature and tool result back
    turn2_messages = [
        Message.user("What is 2+2?"),
        Message.assistant(content="", tool_calls=[tc]),
        Message(role=Role.TOOL, content="4", tool_call_id=tc.id),
    ]

    mock_response2 = MagicMock()
    mock_response2.candidates = []
    mock_response2.text = "The answer is 4."
    mock_genai_client.aio.models.generate_content.return_value = mock_response2

    await adapter.chat(turn2_messages)

    # Verify generate_content was called with correct contents structure
    call_kwargs = mock_genai_client.aio.models.generate_content.call_args.kwargs
    contents = call_kwargs["contents"]
    assert len(contents) == 3

    # User message
    assert contents[0].role == "user"

    # Model message must preserve thought_signature on the function call part
    model_content = contents[1]
    assert model_content.role == "model"
    model_part = model_content.parts[0]
    assert model_part.function_call.name == "calculator"
    assert model_part.thought_signature == b"opaque_thought_sig_xyz"

    # Tool message must have role="user" and function_response name resolved to "calculator"
    tool_content = contents[2]
    assert tool_content.role == "user"
    tool_part = tool_content.parts[0]
    assert tool_part.function_response.name == "calculator"
    assert tool_part.function_response.response == {"result": "4"}


def test_gemini_default_model(mock_genai_client: MagicMock) -> None:
    """Verify GeminiClient defaults to gemini-3.8-flash."""
    adapter = GeminiClient(api_key="fake-key", client=mock_genai_client)
    assert adapter.model == "gemini-3.8-flash"
