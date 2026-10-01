"""FastAPI dependencies for request handling, storage, tools, and agent execution."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentkit.api.auth import get_current_settings
from agentkit.config import Settings
from agentkit.core.agent import Agent, AgentConfig
from agentkit.core.errors import ServiceUnavailableError
from agentkit.core.pg_trace import PostgresTraceSink
from agentkit.core.trace import TraceSink
from agentkit.llm.base import LLMClient
from agentkit.llm.factory import create_llm_client_from_settings
from agentkit.memory.base import MemoryStore
from agentkit.memory.pg_store import PostgresMemoryStore
from agentkit.memory.redis_store import RedisMemoryStore
from agentkit.memory.tiered import TieredMemoryStore
from agentkit.tools.builtin.calculator import calculator
from agentkit.tools.builtin.http_fetch import http_fetch
from agentkit.tools.builtin.read_file import read_file
from agentkit.tools.builtin.sql_readonly import sql_readonly
from agentkit.tools.builtin.web_search import web_search
from agentkit.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession] | None:
    """Retrieve database session factory from app state."""
    return getattr(request.app.state, "session_factory", None)


async def get_db_session(
    factory: async_sessionmaker[AsyncSession] | None = Depends(get_session_factory),
) -> AsyncIterator[AsyncSession]:
    """Yield a scoped database session.

    Fails explicitly when the application has no session factory. Returning without
    yielding would leave FastAPI's dependency solver with an exhausted generator and
    surface as an opaque ``generator didn't yield`` RuntimeError at request time.
    """
    if factory is None:
        raise ServiceUnavailableError(
            "database",
            "Database is not configured. The session factory is absent from application state.",
        )
    async with factory() as session:
        yield session


def get_redis_client(request: Request) -> Redis | None:
    """Retrieve shared Redis connection pool client."""
    return getattr(request.app.state, "redis_client", None)


def get_memory_store(request: Request) -> PostgresMemoryStore | MemoryStore:
    """Retrieve or construct the persistence memory store.

    Requires live PostgreSQL and Redis infrastructure in production. Explicit test
    overrides can be supplied via request.app.state.memory_store.
    """
    if hasattr(request.app.state, "memory_store") and request.app.state.memory_store is not None:
        store: PostgresMemoryStore | MemoryStore = request.app.state.memory_store
        return store

    factory = get_session_factory(request)
    if factory is None:
        raise ServiceUnavailableError(
            "database",
            "Database is not configured. Session factory is absent from application state.",
        )

    redis_client = get_redis_client(request)
    if redis_client is None:
        raise ServiceUnavailableError(
            "redis",
            "Redis is not configured. Redis client is absent from application state.",
        )

    settings: Settings = getattr(request.app.state, "settings", None) or get_current_settings(
        request
    )

    run_store = PostgresMemoryStore(factory)
    session_store = RedisMemoryStore(redis_client, ttl_s=settings.SESSION_TTL_S)
    return TieredMemoryStore(session_store=session_store, run_store=run_store)


def get_trace_sink(request: Request) -> TraceSink:
    """Retrieve or construct the step trace telemetry sink.

    Requires live PostgreSQL infrastructure in production. Explicit test overrides
    can be supplied via request.app.state.trace_sink.
    """
    if hasattr(request.app.state, "trace_sink") and request.app.state.trace_sink is not None:
        sink: TraceSink = request.app.state.trace_sink
        return sink

    factory = get_session_factory(request)
    if factory is None:
        raise ServiceUnavailableError(
            "database",
            "Database is not configured. Session factory is absent from application state.",
        )

    return PostgresTraceSink(factory)


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
    """Resolve configured LLM client.

    Instantiates the configured provider from settings. Explicit test overrides
    can be supplied via request.app.state.llm_client.
    """
    if hasattr(request.app.state, "llm_client") and request.app.state.llm_client is not None:
        client: LLMClient = request.app.state.llm_client
        return client

    return create_llm_client_from_settings(settings)


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
