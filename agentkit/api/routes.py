import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from agentkit.api.auth import verify_api_key
from agentkit.api.deps import (
    get_agent,
    get_memory_store,
    get_tool_registry,
    get_trace_sink,
)
from agentkit.api.schemas import (
    HealthCheckResponse,
    RunCreateRequest,
    RunResponse,
    RunTraceResponse,
    StepTraceResponse,
    ToolSchemaResponse,
    ToolsListResponse,
)
from agentkit.core.agent import Agent
from agentkit.core.errors import RunNotFoundError
from agentkit.core.trace import StepTrace, TraceSink
from agentkit.memory.base import MemoryStore
from agentkit.memory.pg_store import PostgresMemoryStore
from agentkit.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

runs_router = APIRouter(prefix="/runs", tags=["runs"], dependencies=[Depends(verify_api_key)])
router = runs_router  # alias for backwards compatibility
discovery_router = APIRouter(tags=["discovery"])


@router.post("", response_model=RunResponse)
async def create_run(
    payload: RunCreateRequest,
    agent: Agent = Depends(get_agent),
    memory_store: PostgresMemoryStore | MemoryStore = Depends(get_memory_store),
) -> RunResponse:
    """Execute an agent goal and return execution summary."""
    logger.info("Executing run for goal: %s (session_id: %s)", payload.goal, payload.session_id)
    result = await agent.run(goal=payload.goal, session_id=payload.session_id)

    if hasattr(memory_store, "get_run"):
        run_record = await memory_store.get_run(result.run_id)
        if run_record is not None:
            return RunResponse(
                run_id=run_record.id,
                session_id=run_record.session_id,
                goal=run_record.goal,
                status=run_record.status,
                answer=run_record.final_answer,
                failure_reason=run_record.failure_reason,
                total_input_tokens=run_record.total_input_tokens,
                total_output_tokens=run_record.total_output_tokens,
                created_at=run_record.created_at,
                finished_at=run_record.finished_at,
            )

    now = datetime.now(UTC)
    return RunResponse(
        run_id=result.run_id,
        session_id=payload.session_id,
        goal=payload.goal,
        status=result.status.value,
        answer=result.final_answer,
        failure_reason=result.failure_reason,
        total_input_tokens=result.total_input_tokens,
        total_output_tokens=result.total_output_tokens,
        created_at=now,
        finished_at=now,
    )


@router.get("/{run_id}", response_model=RunResponse)
async def get_run_details(
    run_id: str,
    memory_store: PostgresMemoryStore | MemoryStore = Depends(get_memory_store),
) -> RunResponse:
    """Retrieve metadata and execution status for an existing run."""
    if not hasattr(memory_store, "get_run"):
        raise RunNotFoundError(run_id)

    run_record = await memory_store.get_run(run_id)
    if run_record is None:
        raise RunNotFoundError(run_id)

    return RunResponse(
        run_id=run_record.id,
        session_id=run_record.session_id,
        goal=run_record.goal,
        status=run_record.status,
        answer=run_record.final_answer,
        failure_reason=run_record.failure_reason,
        total_input_tokens=run_record.total_input_tokens,
        total_output_tokens=run_record.total_output_tokens,
        created_at=run_record.created_at,
        finished_at=run_record.finished_at,
    )


@router.get("/{run_id}/trace", response_model=RunTraceResponse)
async def get_run_trace(
    run_id: str,
    memory_store: PostgresMemoryStore | MemoryStore = Depends(get_memory_store),
    trace_sink: TraceSink = Depends(get_trace_sink),
) -> RunTraceResponse:
    """Retrieve chronological step execution traces for a run."""
    if hasattr(memory_store, "get_run"):
        run_record = await memory_store.get_run(run_id)
        if run_record is None:
            raise RunNotFoundError(run_id)

    traces: list[StepTrace] = []
    if hasattr(memory_store, "get_run_trace"):
        traces = await memory_store.get_run_trace(run_id)
    elif hasattr(trace_sink, "get_traces"):
        traces = await trace_sink.get_traces(run_id)

    return RunTraceResponse(
        run_id=run_id,
        steps=[
            StepTraceResponse(
                step_no=t.step_no,
                tool_name=t.tool_name,
                args=t.args,
                result=t.result,
                error=t.error,
                latency_ms=t.latency_ms,
                input_tokens=t.input_tokens,
                output_tokens=t.output_tokens,
                timestamp=t.timestamp,
            )
            for t in traces
        ],
    )


@discovery_router.get("/health", response_model=HealthCheckResponse)
async def health_check(request: Request) -> JSONResponse:
    """Verify application liveness and connectivity to database and Redis."""
    status_code = 200
    db_status = "healthy"
    redis_status = "healthy"
    details: dict[str, str] = {}

    # Check Database Engine
    engine = getattr(request.app.state, "db_engine", None)
    if engine is not None:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            db_status = "unhealthy"
            details["database"] = str(exc)
            status_code = 503
    else:
        db_status = "disabled"

    # Check Redis Client
    redis_client = getattr(request.app.state, "redis_client", None)
    if redis_client is not None:
        try:
            ping_res = await redis_client.ping()
            if not ping_res:
                redis_status = "unhealthy"
                details["redis"] = "Ping returned False"
                status_code = 503
        except Exception as exc:
            redis_status = "unhealthy"
            details["redis"] = str(exc)
            status_code = 503
    else:
        redis_status = "disabled"

    overall_status = "healthy" if status_code == 200 else "unhealthy"
    content = HealthCheckResponse(
        status=overall_status,
        database=db_status,
        redis=redis_status,
        details=details,
    ).model_dump()

    return JSONResponse(status_code=status_code, content=content)


@discovery_router.get(
    "/tools",
    response_model=ToolsListResponse,
    dependencies=[Depends(verify_api_key)],
)
async def list_tools(
    tool_registry: ToolRegistry = Depends(get_tool_registry),
) -> ToolsListResponse:
    """List all registered tools and their JSON schemas."""
    schemas = tool_registry.schemas()
    return ToolsListResponse(
        tools=[
            ToolSchemaResponse(
                name=s.name,
                description=s.description,
                parameters=s.parameters,
            )
            for s in schemas
        ]
    )
