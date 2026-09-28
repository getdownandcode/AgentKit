"""FastAPI application entrypoint with lifespan resource management and error handling."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentkit.config import Settings, get_settings
from agentkit.core.errors import (
    AgentKitError,
    LLMProviderError,
    LLMRateLimitError,
    RunTimeoutError,
    ToolNotFoundError,
)

logger = logging.getLogger(__name__)


def map_agentkit_error_status(exc: AgentKitError) -> int:
    """Map domain AgentKitError subclasses to appropriate HTTP status codes."""
    if isinstance(exc, ToolNotFoundError):
        return 404
    if isinstance(exc, RunTimeoutError):
        return 504
    if isinstance(exc, LLMRateLimitError):
        return 429
    if isinstance(exc, LLMProviderError):
        return 502
    return 400


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage lifecycle of database engine, session factory, and Redis connection pool."""
    settings: Settings = getattr(app.state, "settings", None) or get_settings()

    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
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

    if settings is not None:
        app.state.settings = settings

    @app.exception_handler(AgentKitError)
    async def agentkit_error_handler(_request: Request, exc: AgentKitError) -> JSONResponse:
        status_code = map_agentkit_error_status(exc)
        logger.warning("AgentKitError occurred: %s (status %d)", exc, status_code)
        return JSONResponse(status_code=status_code, content={"error": exc.to_dict()})

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
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": "HTTP_ERROR",
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

    return app


app = create_app()
