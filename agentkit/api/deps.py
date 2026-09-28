"""FastAPI dependencies for request handling, storage, tools, and agent execution."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentkit.api.auth import get_current_settings
from agentkit.config import Settings
from agentkit.core.agent import Agent, AgentConfig
from agentkit.core.pg_trace import PostgresTraceSink
from agentkit.core.trace import InMemoryTraceSink, TraceSink
from agentkit.llm.base import LLMClient
from agentkit.llm.fake import FakeLLMClient
from agentkit.llm.gemini import GeminiClient
from agentkit.memory.base import InMemoryMemoryStore, MemoryStore
from agentkit.memory.pg_store import PostgresMemoryStore
from agentkit.tools.builtin.calculator import calculator
from agentkit.tools.builtin.http_fetch import http_fetch
from agentkit.tools.builtin.read_file import read_file
from agentkit.tools.builtin.sql_readonly import sql_readonly
from agentkit.tools.builtin.web_search import web_search
from agentkit.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def get_session_factory(
    request: Request,
) -> async_sessionmaker[AsyncSession] | None:
    """Retrieve database session factory from app state."""
    return getattr(request.app.state, "session_factory", None)


async def get_db_session(
    request: Request,
) -> AsyncIterator[AsyncSession]:
    """Provide a scoped database session."""
    factory = get_session_factory(request)
    if factory is not None:
        async with factory() as session:
            yield session


def get_redis_client(request: Request) -> Redis[Any] | None:
    """Retrieve shared Redis connection pool client."""
    return getattr(request.app.state, "redis_client", None)


def get_memory_store(request: Request) -> PostgresMemoryStore | MemoryStore:
    """Retrieve or construct the persistence memory store."""
    if hasattr(request.app.state, "memory_store") and request.app.state.memory_store is not None:
        store: PostgresMemoryStore | MemoryStore = request.app.state.memory_store
        return store

    factory = get_session_factory(request)
    if factory is not None:
        return PostgresMemoryStore(factory)

    return InMemoryMemoryStore()


def get_trace_sink(request: Request) -> TraceSink:
    """Retrieve or construct the step trace telemetry sink."""
    if hasattr(request.app.state, "trace_sink") and request.app.state.trace_sink is not None:
        sink: TraceSink = request.app.state.trace_sink
        return sink

    factory = get_session_factory(request)
    if factory is not None:
        return PostgresTraceSink(factory)

    return InMemoryTraceSink()


def create_default_tool_registry() -> ToolRegistry:
    """Construct standard ToolRegistry populated with all AgentKit built-in tools."""
    registry = ToolRegistry()
    registry.register(calculator)
    registry.register(read_file)
    registry.register(http_fetch)
    registry.register(sql_readonly)
    registry.register(web_search)
    return registry


def get_tool_registry(request: Request) -> ToolRegistry:
    """Retrieve or create the shared tool registry."""
    if hasattr(request.app.state, "tool_registry") and request.app.state.tool_registry is not None:
        reg: ToolRegistry = request.app.state.tool_registry
        return reg

    registry = create_default_tool_registry()
    request.app.state.tool_registry = registry
    return registry


def get_llm_client(
    request: Request,
    settings: Settings = Depends(get_current_settings),
) -> LLMClient:
    """Resolve configured LLM client."""
    if hasattr(request.app.state, "llm_client") and request.app.state.llm_client is not None:
        client: LLMClient = request.app.state.llm_client
        return client

    if settings.GEMINI_API_KEY:
        return GeminiClient(api_key=settings.GEMINI_API_KEY, model=settings.LLM_MODEL)

    logger.warning("No LLM API keys provided; falling back to FakeLLMClient")
    return FakeLLMClient()


def get_agent(
    request: Request,
    settings: Settings = Depends(get_current_settings),
    llm: LLMClient = Depends(get_llm_client),
    registry: ToolRegistry = Depends(get_tool_registry),
    memory: MemoryStore = Depends(get_memory_store),
    trace: TraceSink = Depends(get_trace_sink),
) -> Agent:
    """Assemble configured Agent instance with all runtime dependencies."""
    if hasattr(request.app.state, "agent") and request.app.state.agent is not None:
        agent: Agent = request.app.state.agent
        return agent

    config = AgentConfig(
        max_steps=settings.MAX_STEPS,
        run_timeout_s=settings.RUN_TIMEOUT_S,
        tool_timeout_s=settings.TOOL_TIMEOUT_S,
    )
    return Agent(
        llm=llm,
        registry=registry,
        memory=memory,
        trace=trace,
        config=config,
    )
