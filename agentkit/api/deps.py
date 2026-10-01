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
from agentkit.core.trace import InMemoryTraceSink, TraceSink
from agentkit.llm.base import LLMClient
from agentkit.llm.factory import create_llm_client_from_settings
from agentkit.llm.fake import FakeLLMClient
from agentkit.llm.retry import RetryingLLMClient
from agentkit.memory.base import InMemoryMemoryStore, MemoryStore
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
    """Retrieve or construct the persistence memory store."""
    if hasattr(request.app.state, "memory_store") and request.app.state.memory_store is not None:
        store: PostgresMemoryStore | MemoryStore = request.app.state.memory_store
        return store

    factory = get_session_factory(request)
    redis_client = get_redis_client(request)
    settings: Settings = getattr(request.app.state, "settings", None) or get_current_settings(
        request
    )

    run_store: PostgresMemoryStore | InMemoryMemoryStore = (
        PostgresMemoryStore(factory) if factory is not None else InMemoryMemoryStore()
    )

    if redis_client is not None:
        session_store = RedisMemoryStore(redis_client, ttl_s=settings.SESSION_TTL_S)
        return TieredMemoryStore(session_store=session_store, run_store=run_store)

    if isinstance(run_store, PostgresMemoryStore):
        return TieredMemoryStore(session_store=InMemoryMemoryStore(), run_store=run_store)

    return run_store


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
    """Resolve the configured LLM client, wrapped with transient-error retries.

    An explicit ``app.state.llm_client`` override always wins so tests and deployments can
    substitute a client. Otherwise the provider is built from settings and any failure is
    surfaced as a 503: falling back to a fake client would answer a misconfigured
    deployment with scripted text and HTTP 200 instead of surfacing the misconfiguration.
    """
    override: LLMClient | None = getattr(request.app.state, "llm_client", None)
    if override is not None:
        return override

    if settings.LLM_PROVIDER.strip().lower() == "fake":
        return FakeLLMClient()

    try:
        client = create_llm_client_from_settings(settings)
    except Exception as exc:
        logger.error("Failed to initialize LLM provider %s: %s", settings.LLM_PROVIDER, exc)
        raise ServiceUnavailableError(
            "llm",
            f"LLM provider '{settings.LLM_PROVIDER}' could not be initialized.",
        ) from exc

    return RetryingLLMClient(
        client,
        max_retries=settings.LLM_MAX_RETRIES,
        base_delay=settings.LLM_RETRY_BASE_DELAY_S,
        max_delay=settings.LLM_RETRY_MAX_DELAY_S,
    )


def get_agent(
    request: Request,
    settings: Settings = Depends(get_current_settings),
    llm: LLMClient = Depends(get_llm_client),
    registry: ToolRegistry = Depends(get_tool_registry),
    memory: MemoryStore = Depends(get_memory_store),
    trace: TraceSink = Depends(get_trace_sink),
) -> Agent:
    """Assemble a configured Agent from the currently resolved dependencies.

    The agent is rebuilt per request rather than memoized on ``app.state``: caching the first
    assembled instance would pin every later request to that request's LLM client, registry,
    memory store and trace sink, silently ignoring any later override or settings change.
    """
    override: Agent | None = getattr(request.app.state, "agent_override", None)
    if override is not None:
        return override

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
