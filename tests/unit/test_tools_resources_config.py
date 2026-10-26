"""Regression tests for tool schemas, LLM client lifecycle and wired settings.

Covers AUDIT-13 (dropped ``$defs``), AUDIT-14 (per-request provider clients) and AUDIT-12
(``TOOL_OUTPUT_MAX_CHARS`` and the documented compose settings).
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from agentkit.api import deps
from agentkit.api.deps import get_agent, get_llm_client
from agentkit.api.main import create_app
from agentkit.config import Settings
from agentkit.core.trace import InMemoryTraceSink
from agentkit.llm.base import LLMResponse
from agentkit.llm.fake import FakeLLMClient
from agentkit.memory.base import InMemoryMemoryStore
from agentkit.tools.models import ToolCall
from agentkit.tools.registry import ToolRegistry
from agentkit.tools.schema import create_tool_schema

ROOT = Path(__file__).parents[2]


# --- AUDIT-13: $ref / $defs -----------------------------------------------------------------


class Mode(Enum):
    """A parameter enum, emitted by Pydantic as a $ref into $defs."""

    FAST = "fast"
    SAFE = "safe"


class Filter(BaseModel):
    """A nested model parameter, also emitted as a $ref."""

    field: str
    value: str


def _nested_tool(mode: Mode, filter: Filter) -> str:  # noqa: A002 - mirrors a schema keyword
    """A tool whose parameters are an enum and a nested model."""
    return f"{mode}:{filter.field}"


def _resolve_refs(node: Any, document: dict[str, Any]) -> list[str]:
    """Return the $refs in ``node`` that do not resolve inside ``document``."""
    dangling: list[str] = []
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            target: Any = document
            for part in ref[2:].split("/"):
                if not isinstance(target, dict) or part not in target:
                    dangling.append(ref)
                    break
                target = target[part]
        for value in node.values():
            dangling.extend(_resolve_refs(value, document))
    elif isinstance(node, list):
        for item in node:
            dangling.extend(_resolve_refs(item, document))
    return dangling


def test_nested_model_and_enum_refs_resolve_inside_the_emitted_schema() -> None:
    """A schema whose $refs cannot resolve is rejected or mis-handled by providers."""
    schema, _ = create_tool_schema(_nested_tool, name="nested_tool")

    assert "$defs" in schema.parameters
    assert _resolve_refs(schema.parameters, schema.parameters) == []
    assert {"Mode", "Filter"} <= set(schema.parameters["$defs"])


def test_simple_builtin_schemas_are_unchanged() -> None:
    """Existing tools must keep the shape providers already accept."""
    schema, _ = create_tool_schema(_nested_tool, name="nested_tool")
    assert list(schema.parameters)[:2] == ["type", "properties"]

    def simple(expression: str) -> str:
        """Evaluate an expression."""
        return expression

    simple_schema, _ = create_tool_schema(simple, name="simple")
    assert simple_schema.parameters == {
        "type": "object",
        "properties": {
            "expression": {"title": "Expression", "type": "string"},
        },
        "required": ["expression"],
    }


# --- AUDIT-14: provider client lifecycle -----------------------------------------------------


class _StubClient:
    """Provider client double that records whether it was closed."""

    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def _request(app: Any) -> Any:
    return SimpleNamespace(app=app)


def test_provider_client_is_created_once_and_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every client owns a transport, so per-request construction leaks sockets."""
    app = create_app(settings=Settings(API_KEYS="k"))
    created: list[_StubClient] = []

    def _factory(_settings: Settings) -> _StubClient:
        client = _StubClient()
        created.append(client)
        return client

    monkeypatch.setattr(deps, "create_llm_client_from_settings", _factory)
    settings = Settings(API_KEYS="k")

    first = get_llm_client(cast(Any, _request(app)), settings)
    second = get_llm_client(cast(Any, _request(app)), settings)

    assert first is second
    assert len(created) == 1


def test_explicit_override_still_wins() -> None:
    """A test-supplied client must not be replaced by a managed one."""
    app = create_app(settings=Settings(API_KEYS="k"))
    override = FakeLLMClient()
    app.state.llm_client = override

    resolved = get_llm_client(cast(Any, _request(app)), Settings(API_KEYS="k"))

    assert resolved is override


def test_managed_client_is_closed_on_shutdown() -> None:
    """The lifespan must close the transport it owns."""
    app = create_app(settings=Settings(API_KEYS="k"))
    stub = _StubClient()
    app.state.managed_llm_client = stub

    with TestClient(app):
        pass

    assert stub.closed is True
    assert app.state.managed_llm_client is None


# --- AUDIT-12: wired settings ----------------------------------------------------------------


def test_tool_output_max_chars_reaches_the_agent_config() -> None:
    """The setting existed but was never read."""
    settings = Settings(API_KEYS="k", TOOL_OUTPUT_MAX_CHARS=50)
    app = create_app(settings=settings)
    request = cast(Any, _request(app))

    agent = get_agent(
        request,
        settings,
        FakeLLMClient(),
        ToolRegistry(),
        InMemoryMemoryStore(),
        InMemoryTraceSink(),
    )

    assert agent.config.tool_output_max_chars == 50


@pytest.mark.asyncio
async def test_tool_output_max_chars_truncates_real_tool_output() -> None:
    """The configured limit must be enforced by the executor, not just stored."""
    long_output = "x" * 500

    def spew() -> str:
        """Return a long string."""
        return long_output

    registry = ToolRegistry()
    registry.register(spew, name="spew")

    llm = FakeLLMClient(
        responses=[
            LLMResponse(tool_calls=[ToolCall(id="c1", name="spew", arguments={})]),
            LLMResponse(text="done"),
        ]
    )
    settings = Settings(API_KEYS="k", TOOL_OUTPUT_MAX_CHARS=50)
    app = create_app(settings=settings)
    agent = get_agent(
        cast(Any, _request(app)),
        settings,
        llm,
        registry,
        InMemoryMemoryStore(),
        InMemoryTraceSink(),
    )

    await agent.run(goal="spew something")

    tool_messages = [m for m in llm.history[-1] if m.role.value == "tool"]
    assert tool_messages, "the tool result never reached the model"
    assert tool_messages[0].content.startswith("x" * 50)
    assert "Output truncated" in tool_messages[0].content


def _documented_settings() -> set[str]:
    """Return the variable names documented in .env.example."""
    names: set[str] = set()
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        names.add(stripped.split("=", 1)[0].strip())
    return names


def test_compose_forwards_every_documented_setting() -> None:
    """A documented setting that compose silently drops is dead configuration."""
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    missing = sorted(
        name
        for name in _documented_settings()
        if re.search(rf"\b{re.escape(name)}\b", compose) is None
    )
    assert missing == [], f"documented but not wired in compose: {missing}"
