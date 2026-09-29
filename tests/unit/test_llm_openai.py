from unittest.mock import AsyncMock, MagicMock

import openai
import pytest

from agentkit.core.errors import AuthenticationError, LLMProviderError, RateLimitExceededError
from agentkit.llm.base import Message
from agentkit.llm.openai import OpenAIClient
from agentkit.tools.models import ToolCall, ToolSchema


@pytest.fixture
def mock_openai_client() -> MagicMock:
    client = MagicMock()
    client.chat.completions.create = AsyncMock()
    return client


def test_openai_client_init_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(AuthenticationError):
        OpenAIClient(api_key=None)


def test_openai_client_init_with_key() -> None:
    adapter = OpenAIClient(api_key="sk-test-12345", model="gpt-4o")
    assert adapter.model == "gpt-4o"
    assert adapter.api_key == "sk-test-12345"


@pytest.mark.asyncio
async def test_openai_client_chat_text_response(mock_openai_client: MagicMock) -> None:
    adapter = OpenAIClient(client=mock_openai_client, model="gpt-4o-mini")

    mock_choice = MagicMock()
    mock_choice.message.content = "Paris is the capital of France."
    mock_choice.message.tool_calls = None

    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 15
    mock_usage.completion_tokens = 8

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = mock_usage
    mock_response.model_dump.return_value = {"id": "chatcmpl-123"}

    mock_openai_client.chat.completions.create.return_value = mock_response

    messages = [
        Message.system("You are a helpful assistant."),
        Message.user("What is the capital of France?"),
    ]

    response = await adapter.chat(messages=messages)

    assert response.text == "Paris is the capital of France."
    assert response.tool_calls == []
    assert response.usage.input_tokens == 15
    assert response.usage.output_tokens == 8

    # Verify converted messages sent to OpenAI
    call_kwargs = mock_openai_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-mini"
    assert call_kwargs["messages"] == [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is the capital of France?"},
    ]


@pytest.mark.asyncio
async def test_openai_client_chat_tool_calls(mock_openai_client: MagicMock) -> None:
    adapter = OpenAIClient(client=mock_openai_client)

    mock_tc = MagicMock()
    mock_tc.id = "call_xyz123"
    mock_tc.function.name = "calculator"
    mock_tc.function.arguments = '{"expression": "40 + 2"}'

    mock_choice = MagicMock()
    mock_choice.message.content = None
    mock_choice.message.tool_calls = [mock_tc]

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = MagicMock(prompt_tokens=25, completion_tokens=12)
    mock_response.model_dump.return_value = {}

    mock_openai_client.chat.completions.create.return_value = mock_response

    tool_schemas = [
        ToolSchema(
            name="calculator",
            description="Perform arithmetic",
            parameters={
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
        )
    ]

    messages = [
        Message.user("Calculate 40 + 2"),
    ]

    response = await adapter.chat(messages=messages, tools=tool_schemas)

    assert len(response.tool_calls) == 1
    tc = response.tool_calls[0]
    assert tc.id == "call_xyz123"
    assert tc.name == "calculator"
    assert tc.arguments == {"expression": "40 + 2"}

    call_kwargs = mock_openai_client.chat.completions.create.call_args.kwargs
    assert "tools" in call_kwargs
    assert call_kwargs["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "calculator",
                "description": "Perform arithmetic",
                "parameters": {
                    "type": "object",
                    "properties": {"expression": {"type": "string"}},
                    "required": ["expression"],
                },
            },
        }
    ]


@pytest.mark.asyncio
async def test_openai_client_message_conversion(mock_openai_client: MagicMock) -> None:
    adapter = OpenAIClient(client=mock_openai_client)

    mock_choice = MagicMock()
    mock_choice.message.content = "Final answer"
    mock_choice.message.tool_calls = None
    mock_response = MagicMock(choices=[mock_choice], usage=None)
    mock_response.model_dump.return_value = {}
    mock_openai_client.chat.completions.create.return_value = mock_response

    messages = [
        Message.system("Directive"),
        Message.user("Query"),
        Message.assistant(
            content="Invoking tool",
            tool_calls=[ToolCall(id="call_1", name="search", arguments={"q": "test"})],
        ),
        Message.tool_result(tool_call_id="call_1", content="Result data"),
    ]

    await adapter.chat(messages=messages)

    call_kwargs = mock_openai_client.chat.completions.create.call_args.kwargs
    sent_msgs = call_kwargs["messages"]
    assert len(sent_msgs) == 4
    assert sent_msgs[0] == {"role": "system", "content": "Directive"}
    assert sent_msgs[1] == {"role": "user", "content": "Query"}
    assert sent_msgs[2]["role"] == "assistant"
    assert sent_msgs[2]["content"] == "Invoking tool"
    assert sent_msgs[2]["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "search", "arguments": '{"q": "test"}'},
        }
    ]
    assert sent_msgs[3] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "Result data",
    }


@pytest.mark.asyncio
async def test_openai_client_error_handling(mock_openai_client: MagicMock) -> None:
    adapter = OpenAIClient(client=mock_openai_client)

    # Authentication error
    mock_openai_client.chat.completions.create.side_effect = openai.AuthenticationError(
        message="Invalid API Key",
        response=MagicMock(status_code=401),
        body=None,
    )
    with pytest.raises(AuthenticationError):
        await adapter.chat(messages=[Message.user("hi")])

    # Rate limit error
    mock_openai_client.chat.completions.create.side_effect = openai.RateLimitError(
        message="Quota exceeded",
        response=MagicMock(status_code=429),
        body=None,
    )
    with pytest.raises(RateLimitExceededError):
        await adapter.chat(messages=[Message.user("hi")])

    # General provider error
    mock_openai_client.chat.completions.create.side_effect = openai.APIConnectionError(
        request=MagicMock()
    )
    with pytest.raises(LLMProviderError):
        await adapter.chat(messages=[Message.user("hi")])
