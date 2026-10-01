"""FastAPI application entrypoint with lifespan resource management and error handling."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from agentkit.api.routes import discovery_router
from agentkit.api.routes import router as runs_router
from agentkit.config import Settings, get_settings
from agentkit.core.errors import (
    AgentKitError,
    AuthenticationError,
    LLMProviderError,
    LLMRateLimitError,
    RateLimitExceededError,
    RunNotFoundError,
    RunTimeoutError,
    ServiceUnavailableError,
    ToolNotFoundError,
)
from agentkit.db.session import create_engine, create_session_factory

logger = logging.getLogger(__name__)


def map_agentkit_error_status(exc: AgentKitError) -> int:
    """Map domain AgentKitError subclasses to appropriate HTTP status codes."""
    if isinstance(exc, AuthenticationError):
        return 401
    if isinstance(exc, (ToolNotFoundError, RunNotFoundError)):
        return 404
    if isinstance(exc, RunTimeoutError):
        return 504
    if isinstance(exc, (LLMRateLimitError, RateLimitExceededError)):
        return 429
    if isinstance(exc, LLMProviderError):
        return 502
    if isinstance(exc, ServiceUnavailableError):
        return 503
    return 400


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage lifecycle of database engine, session factory, and Redis connection pool."""
    settings: Settings = getattr(app.state, "settings", None) or get_settings()

    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    session_factory = create_session_factory(engine)
    redis_client: Redis[Any] = Redis.from_url(settings.REDIS_URL, decode_responses=True)

    app.state.db_engine = engine
    app.state.session_factory = session_factory
    app.state.redis_client = redis_client
    logger.info("Application lifespan initialized (DB & Redis connections established).")

    try:
        yield
    finally:
        aclose_fn = getattr(redis_client, "aclose", None)
        if callable(aclose_fn):
            await aclose_fn()
        else:
            await redis_client.close()
        await engine.dispose()
        logger.info("Application lifespan torn down (DB & Redis resources released).")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure FastAPI application instance."""
    app = FastAPI(
        title="AgentKit API",
        version="0.1.0",
        description="Autonomous AI agent runtime service.",
        lifespan=lifespan,
    )

    app.state.settings = settings if settings is not None else get_settings()

    @app.exception_handler(AgentKitError)
    async def agentkit_error_handler(_request: Request, exc: AgentKitError) -> JSONResponse:
        status_code = map_agentkit_error_status(exc)
        logger.warning("AgentKitError occurred: %s (status %d)", exc, status_code)
        headers: dict[str, str] = {}
        if isinstance(exc, RateLimitExceededError):
            headers["Retry-After"] = str(exc.retry_after)
        return JSONResponse(
            status_code=status_code, content={"error": exc.to_dict()}, headers=headers
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        logger.warning("Request validation error: %s", exc)
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Input validation failed: " + str(exc),
                }
            },
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        logger.info("HTTP exception: %d - %s", exc.status_code, exc.detail)
        code = "RATE_LIMIT_EXCEEDED" if exc.status_code == 429 else "HTTP_ERROR"
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={
                "error": {
                    "code": code,
                    "message": str(exc.detail),
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled server exception: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "An unexpected internal server error occurred.",
                }
            },
        )

    app.include_router(runs_router)
    app.include_router(discovery_router)

    return app


app = create_app()
