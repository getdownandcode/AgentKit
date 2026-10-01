# AgentKit Execution Progress Log

This document tracks execution progress across all backlog tasks, including pull request numbers, commit counts, status, and notes.

| Task ID | Milestone | Title | Branch | PR # | Commits | Status | Notes & Blockers |
|---------|-----------|-------|--------|------|---------|--------|------------------|
| M0-T01  | M0        | Repository Setup & Context Scaffolding | `main` | N/A | 7 | Done | Initial warm-up repository scaffolding |
| M0-T02  | M0        | Configuration Management | `feat/config-settings` | #1 | 2 | Done | Pydantic BaseSettings with env validation |
| M0-T03  | M0        | Domain Errors & Core Base Types | `feat/core-errors-types` | #2 | 2 | Done | AgentKitError hierarchy, to_dict, StrEnum types |
| M1-T01  | M1        | Tool Models & ToolResult Specification | `feat/tool-models` | #3 | 2 | Done | ToolResult, ToolCall, and ToolSchema models |
| M1-T02  | M1        | Signature Inspection & JSON Schema Generator | `feat/tool-schema-generator` | #4 | 2 | Done | Dynamic Pydantic schema generator from callables |
| M1-T03  | M1        | @tool Decorator & Registry Storage | `feat/tool-registry-decorator` | #5 | 2 | Done | ToolRegistry and generic @tool decorator |
| M1-T04  | M1        | Safe Tool Execution Engine | `feat/tool-executor` | #6 | 2 | Done | Safe execution with threadpool, timeouts, truncation |
| M2-T01  | M2        | Abstract LLMClient Interface | `feat/llm-client-base` | #7 | 2 | Done | LLMClient ABC, Message, TokenUsage, LLMResponse |
| M2-T02  | M2        | Scripted FakeLLMClient | `feat/fake-llm-client` | #8 | 2 | Done | Deterministic FIFO mock client with history tracking |
| M2-T03  | M2        | Google Gemini SDK Adapter | `feat/gemini-adapter` | #9 | 2 | Done | GeminiClient with FunctionDeclaration & token metrics |
| M3-T01  | M3        | Agent State Machine & Configuration | `feat/agent-state-machine` | #10 | 2 | Done | Agent class, AgentConfig, and RunResult models |
| M3-T02  | M3        | Core ReAct Reasoning Step Loop | `feat/react-step-loop` | #11 | 2 | Done | Multi-step ReAct loop with tool invocation & answer exit |
| M3-T03  | M3        | Loop Termination & Timeout Guards | `feat/loop-guards` | #12 | 2 | Done | asyncio.timeout wrapper, max steps & timeout RunStatus handling |
| M3-T04  | M3        | Duplicate Tool Call Loop Detection | `feat/duplicate-tool-loop-detector` | #13 | 2 | Done | Infinite loop detection with canonical args & threshold abort |
| M4-T01  | M4        | Safe AST Calculator Tool | `feat/safe-calculator-tool` | #14 | 2 | Done | Strict AST arithmetic parser with zero eval/exec and DoS guards |
| M4-T02  | M4        | Sandboxed File Reader Tool | `feat/sandboxed-file-reader-tool` | #15 | 2 | Done | FILE_TOOL_BASE_DIR sandboxing, path traversal & symlink defense |
| M4-T03  | M4        | SSRF-Protected HTTP Fetch Tool | `feat/ssrf-http-fetch-tool` | #16 | 2 | Done | DNS & IP SSRF validation, private/metadata blocking, safe redirect |
| M4-T04  | M4        | Read-Only SQL Tool | `feat/readonly-sql-tool` | #17 | 2 | Done | Strict SELECT AST validation, DDL/DML rejection, Markdown formatting |
| M4-T05  | M4        | Web Search Tool Adapter | `feat/web-search-tool` | #18 | 2 | Done | Tavily search integration, error handling for missing key, formatted snippets |
| M5-T01  | M5        | TraceSink Protocol & InMemoryTraceSink | `feat/trace-sink-base` | #19 | 2 | Done | StepTrace model, TraceSink ABC, InMemoryTraceSink, Agent.run() trace wiring |
| M5-T02  | M5        | Contextual Structured JSON Logger | `feat/structured-logging` | #20 | 2 | Done | JSONFormatter, contextvars run_id/step_no propagation, Agent logging |
| M5-T03  | M5        | PostgreSQL Trace Sink | `feat/postgres-trace-sink` | #21 | 2 | Done | PostgresTraceSink, Run & Step SQLAlchemy models, async SQLite test suite |
| M6-T01  | M6        | Abstract MemoryStore Interface | `feat/memory-store-base` | #22 | 2 | Done | MemoryStore ABC, immutable RunRecord model, InMemoryMemoryStore |
| M6-T02  | M6        | Async Redis Session Store | `feat/redis-memory-store` | #23 | 2 | Done | RedisMemoryStore, session key TTL, sliding window truncation |
| M6-T03  | M6        | Database Models & Alembic Migrations | `feat/alembic-migrations` | #24 | 2 | Done | alembic.ini, env.py, 001_initial migration, upgrade/downgrade test |
| M6-T04  | M6        | PostgreSQL Run & Step Repository | `feat/pg-memory-store` | #25 | 2 | Done | PostgresMemoryStore, run lifecycle, trace retrieval, session filters |
| M7-T01  | M7        | FastAPI Application Core & Handlers | `feat/api-core-app` | #26 | 2 | Done | create_app factory, async lifespan, global error handlers for JSON specs |
| M7-T02  | M7        | API Key Authentication Dependency | `feat/api-auth` | #27 | 2 | Done | X-API-Key validation, /health bypass, 401 error responses |
| M7-T03  | M7        | Run Management Routes | `feat/api-runs-routes` | #28 | 2 | Done | POST /runs, GET /runs/{run_id}, GET /runs/{run_id}/trace |
| M7-T04  | M7        | Discovery & Health Routes | `feat/api-tools-health` | #29 | 2 | Done | GET /tools, GET /health verifying live Postgres and Redis connectivity |
| M8-T01  | M8        | OpenAI SDK Adapter | `feat/openai-adapter` | #30 | 2 | Done | OpenAIClient with function calling, choices parsing, TokenUsage |
| M8-T02  | M8        | Multi-Provider LLM Factory | `feat/llm-provider-factory` | #31 | 2 | Done | create_llm_client and create_llm_client_from_settings factory |
| M9-T01  | M9        | Exponential Backoff Retry Wrapper | `feat/llm-retry-wrapper` | #32 | 2 | Done | LLMRetryWrapper with exponential jitter and retryable error classification |
| M9-T02  | M9        | Redis Sliding-Window Rate Limiter | `feat/redis-rate-limiter` | #33 | 2 | Done | Per-key sliding-window rate limiting dependency |
| M9-T03  | M9        | Run Execution Timeout Guards | `feat/agent-execution-timeouts` | #34 | 2 | Done | asyncio.timeout guardrail and TIMED_OUT status transition |
| M10-T01 | M10       | Tool Failure Self-Correction Test | `test/tool-failure-recovery` | #35 | 2 | Done | Multi-turn self-correction and parameter error recovery |
| M10-T02 | M10       | Prompt Injection Resistance Test | `test/prompt-injection-resistance` | #36 | 2 | Done | Quarantining adversarial injection payloads within Role.TOOL turns |
| M10-T03 | M10       | Built-in Tools Security Suite | `test/builtin-tools-security` | #37 | 2 | Done | Security unit tests covering AST evasion, SQL injection, path traversal, SSRF |
| M11-T01 | M11       | Multi-Stage Production Dockerfile | `feat/dockerfile-production` | #38 | 2 | Done | Multi-stage python:3.12-slim build with non-root user and curl healthcheck |
| M11-T02 | M11       | Docker Compose Environment | `feat/docker-compose-env` | #39 | 2 | Done | Complete docker-compose with postgres, redis, api, volume persistence |
| M11-T03 | M11       | GitHub Actions CI Pipeline | `feat/github-actions-ci` | #40 | 2 | Done | Lint, test matrix (Python 3.11, 3.12) with Postgres and Redis service containers |
| M12-T01 | M12       | Demo Database Seed & End-to-End Demo | `feat/demo-script` | #41 | 2 | Done | ANSI SQL seed, multi-tool analytical demo script with offline support |
| M12-T02 | M12       | AWS Deployment Guide | `docs/aws-deployment-guide` | #42 | 2 | Done | Single-node EC2 production guide with security groups, systemd, Caddy/Nginx TLS |
| M12-T03 | M12       | Comprehensive Portfolio README | `docs/portfolio-readme` | #43 | 2 | Done | Architecture Mermaid diagram, quickstart, 10-line @tool guide, ADRs summary |
| M13-T01 | M13       | Real-Infrastructure Test Tier | `fix/real-infrastructure-verification` | #44 | 1 | Done | Opt-in live test tier against real PostgreSQL and Redis |
| M13-T02 | M13       | Atomic Sliding-Window Rate Limiting | `fix/real-infrastructure-verification` | #44 | 1 | Done | Atomic Lua script eliminating 19/20 concurrency bypass |
| M13-T03 | M13       | CI Split, Compose Hardening | `fix/real-infrastructure-verification` | #44 | 1 | Done | CI live service job, strict compose API_KEYS, demo persistence |
| M13-T04 | M13       | Gemini Tool Calling & Active Model Standard | `feat/gemini-tool-calling-fix` | #45 | 2 | Done | Thought signature preservation, tool role, gemini-2.0-flash default |
| M13-T05 | M13       | Idempotent Demo Seeding & Compose Auto-Seed | `feat/docker-compose-seed` | #46 | 2 | Done | ON CONFLICT DO NOTHING, scripts/seed.py, compose boot auto-seed |
| M13-T06 | M13       | Whole-Project Audit Remediation | `fix/audit-hardening` | #47 | 2 | Done | Fail-closed config and LLM resolution, SSRF post-connect check, atomic fallback |

---

## Detailed Task Notes

### M0-T01: Repository Setup & Context Scaffolding
- **Status**: Done
- **Notes**: Establishing baseline configuration, documentation, development standards (`AGENTS.md`), architecture definition, and initial backlog.

### M0-T02: Configuration Management
- **Status**: Done
- **PR**: #1
- **Commits**: 2
- **Notes**: Implemented `agentkit/config.py` with Settings, field constraints, API keys list helper, and cached getter. Unit tests in `tests/unit/test_config.py`.

### M0-T03: Domain Errors & Core Base Types
- **Status**: Done
- **PR**: #2
- **Commits**: 2
- **Notes**: Implemented `agentkit/core/errors.py` and `agentkit/core/types.py` with StrEnum and structured error serialization. Unit tests in `tests/unit/test_errors_types.py`.

### M1-T01: Tool Models & ToolResult Specification
- **Status**: Done
- **PR**: #3
- **Commits**: 2
- **Notes**: Implemented frozen Pydantic models for ToolResult, ToolCall, and ToolSchema in `agentkit/tools/models.py`. Unit tests in `tests/unit/test_tool_models.py`.

### M1-T02: Signature Inspection & JSON Schema Generator
- **Status**: Done
- **PR**: #4
- **Commits**: 2
- **Notes**: Implemented `create_tool_schema` in `agentkit/tools/schema.py` using inspect and dynamic Pydantic model creation. Unit tests in `tests/unit/test_tool_schema.py`.

### M1-T03: @tool Decorator & Registry Storage
- **Status**: Done
- **PR**: #5
- **Commits**: 2
- **Notes**: Implemented `ToolRegistry` and `@tool` with generic type-preserving overloads in `agentkit/tools/registry.py`. Unit tests in `tests/unit/test_tool_registry.py`.

### M1-T04: Safe Tool Execution Engine
- **Status**: Done
- **PR**: #6
- **Commits**: 2
- **Notes**: Implemented `execute_tool` in `agentkit/tools/executor.py` and `ToolRegistry.execute()` with sync threadpool dispatch, timeouts, truncation, and error wrapping. Unit tests in `tests/unit/test_tool_executor.py`.

### M2-T01: Abstract LLMClient Interface
- **Status**: Done
- **PR**: #7
- **Commits**: 2
- **Notes**: Implemented `LLMClient` ABC, `Message`, `TokenUsage`, and `LLMResponse` in `agentkit/llm/base.py`. Unit tests in `tests/unit/test_llm_base.py`.

### M2-T02: Scripted FakeLLMClient
- **Status**: Done
- **PR**: #8
- **Commits**: 2
- **Notes**: Implemented `FakeLLMClient` in `agentkit/llm/fake.py` supporting FIFO scripted responses, queue helpers, call history, and tools history. Unit tests in `tests/unit/test_fake_llm.py`.

### M2-T03: Google Gemini SDK Adapter
- **Status**: Done
- **PR**: #9
- **Commits**: 2
- **Notes**: Implemented `GeminiClient` in `agentkit/llm/gemini.py` with google-genai SDK, FunctionDeclaration translation, candidate parsing, and usage accounting. Unit tests in `tests/unit/test_gemini_adapter.py`.

### M3-T01: Agent State Machine & Configuration
- **Status**: Done
- **PR**: #10
- **Commits**: 2
- **Notes**: Implemented `AgentConfig`, `RunResult`, and `Agent` class in `agentkit/core/agent.py`. Unit tests in `tests/unit/test_agent.py`.

### M3-T02: Core ReAct Reasoning Step Loop
- **Status**: Done
- **PR**: #11
- **Commits**: 2
- **Notes**: Implemented ReAct step loop with tool execution, token accumulation, trace hooks, and answer exit in `agentkit/core/agent.py`. Unit tests in `tests/unit/test_agent_loop.py`.

### M3-T03: Loop Termination & Timeout Guards
- **Status**: Done
- **PR**: #12
- **Commits**: 2
- **Notes**: Implemented `asyncio.timeout(config.run_timeout_s)` guard, `RunStatus.TIMED_OUT`, `RunStatus.MAX_STEPS_EXCEEDED`, and optional `raise_on_failure` in `agentkit/core/agent.py`. Updated `RunTimeoutError` signature in `agentkit/core/errors.py`. Unit tests in `tests/unit/test_agent_guards.py`.

### M3-T04: Duplicate Tool Call Loop Detection
- **Status**: Done
- **PR**: #13
- **Commits**: 2
- **Notes**: Implemented `DuplicateToolCallLoopError` in `agentkit/core/errors.py`, `max_consecutive_duplicate_tool_calls` in `AgentConfig`, canonical JSON argument serialization, and loop aborting logic in `agentkit/core/agent.py`. Unit tests in `tests/unit/test_agent_loop_detector.py`.

### M4-T01: Safe AST Calculator Tool
- **Status**: Done
- **PR**: #14
- **Commits**: 2
- **Notes**: Implemented `calculator` tool and `safe_calculate` in `agentkit/tools/builtin/calculator.py` using Python `ast` parsing, operator whitelist, node count limit, and exponent limit. 24 unit and security tests in `tests/unit/test_tool_calculator.py`.

### M4-T02: Sandboxed File Reader Tool
- **Status**: Done
- **PR**: #15
- **Commits**: 2
- **Notes**: Implemented `read_file` tool and `read_sandboxed_file` in `agentkit/tools/builtin/read_file.py` with strict `FILE_TOOL_BASE_DIR` sandboxing, symlink escape defenses, path traversal protection, size caps, and UTF-8 verification. Unit tests in `tests/unit/test_tool_read_file.py`.

### M4-T03: SSRF-Protected HTTP Fetch Tool
- **Status**: Done
- **PR**: #16
- **Commits**: 2
- **Notes**: Implemented `http_fetch` tool and `validate_url_ssrf` in `agentkit/tools/builtin/http_fetch.py` blocking loopback, RFC1918 private subnets, cloud metadata (169.254.169.254), non-HTTP/HTTPS schemes, with per-hop redirect re-validation. Unit tests in `tests/unit/test_tool_http_fetch.py`.

### M4-T04: Read-Only SQL Tool
- **Status**: Done
- **PR**: #17
- **Commits**: 2
- **Notes**: Implemented `sql_readonly` tool, `validate_sql_query`, and `execute_sql_query` in `agentkit/tools/builtin/sql_readonly.py` restricting execution strictly to single SELECT or WITH statements, blocking comments and DDL/DML, capping row count, and formatting results as Markdown tables. Unit tests in `tests/unit/test_tool_sql_readonly.py`.

### M4-T05: Web Search Tool Adapter
- **Status**: Done
- **PR**: #18
- **Commits**: 2
- **Notes**: Implemented `web_search` tool and `search_web` in `agentkit/tools/builtin/web_search.py` connecting to Tavily search API, limiting results, handling missing API keys with informative errors, and formatting output as numbered snippets. Unit tests in `tests/unit/test_tool_web_search.py`.

### M5-T01: TraceSink Protocol & InMemoryTraceSink
- **Status**: Done
- **PR**: #19
- **Commits**: 2
- **Notes**: Implemented `StepTrace` frozen Pydantic model and `TraceSink` abstract interface in `agentkit/core/trace.py`, along with `InMemoryTraceSink` and execution trace hook integration in `Agent.run()`. Unit tests in `tests/unit/test_trace.py`.

### M5-T02: Contextual Structured JSON Logger
- **Status**: Done
- **PR**: #20
- **Commits**: 2
- **Notes**: Implemented `JSONFormatter` and async-safe context propagation (`log_context`, `set_current_run_id`, `set_current_step_no`) in `agentkit/core/log.py`. Integrated contextual logging into `Agent.run()` so that all log records emitted during a run are automatically tagged with `run_id` and sequential `step_no`. Unit tests in `tests/unit/test_log.py`.

### M5-T03: PostgreSQL Trace Sink
- **Status**: Done
- **PR**: #21
- **Commits**: 2
- **Notes**: Implemented `PostgresTraceSink` in `agentkit/core/pg_trace.py` and SQLAlchemy 2.0 `Run` and `Step` models in `agentkit/db/models.py` with composite index on `(run_id, step_no)` and defensive parent run creation. Unit tests with in-memory async SQLite in `tests/unit/test_pg_trace.py`.

### M6-T01: Abstract MemoryStore Interface
- **Status**: Done
- **PR**: #22
- **Commits**: 2
- **Notes**: Implemented abstract `MemoryStore` ABC in `agentkit/memory/base.py` for session conversation messages and run state persistence, along with `RunRecord` immutable model and `InMemoryMemoryStore` for local/test execution. Unit tests in `tests/unit/test_memory_base.py`.

### M6-T02: Async Redis Session Store
- **Status**: Done
- **PR**: #23
- **Commits**: 2
- **Notes**: Implemented `RedisMemoryStore` in `agentkit/memory/redis_store.py` providing session message serialization, configurable TTL (`SESSION_TTL_S`), and sliding-window history truncation preserving the initial system prompt. Unit tests with `fakeredis` in `tests/unit/test_redis_store.py`.

### M6-T03: Database Models & Alembic Migrations
- **Status**: Done
- **PR**: #24
- **Commits**: 2
- **Notes**: Configured Alembic with `alembic.ini`, `migrations/env.py` (with support for asyncpg and sync drivers), and initial schema revision `001_initial_runs_and_steps.py` establishing `runs` and `steps` tables, foreign keys, and indexes. Integration test in `tests/integration/test_migrations.py`.

### M6-T04: PostgreSQL Run & Step Repository
- **Status**: Done
- **PR**: #25
- **Commits**: 2
- **Notes**: Implemented `PostgresMemoryStore` in `agentkit/memory/pg_store.py` providing asynchronous run creation, status and metrics updating, single run retrieval, step trace querying ordered by step number, and session-filtered run listing. Unit tests with in-memory SQLite in `tests/unit/test_pg_store.py`.

### M7-T01: FastAPI Application Core & Handlers
- **Status**: Done
- **PR**: #26
- **Commits**: 2
- **Notes**: Implemented `create_app` factory in `agentkit/api/main.py` with async lifespan context management for database engines and Redis connection pools, plus unified global exception handlers translating `AgentKitError`, `RequestValidationError`, `HTTPException`, and server exceptions into standard `{"error": {"code": "...", "message": "..."}}` responses. Unit tests in `tests/unit/test_api_core.py`.

### M7-T02: API Key Authentication Dependency
- **Status**: Done
- **PR**: #27
- **Commits**: 2
- **Notes**: Implemented `verify_api_key` security dependency in `agentkit/api/auth.py` validating incoming `X-API-Key` headers against configured `API_KEYS`. Mapped `AuthenticationError` to 401 Unauthorized in global exception handlers. Configured ruff for immutable FastAPI `Security` / `Depends` calls. Unit tests in `tests/unit/test_api_auth.py`.

### M7-T03: Run Management Routes
- **Status**: Done
- **PR**: #28
- **Commits**: 2
- **Notes**: Implemented `POST /runs`, `GET /runs/{run_id}`, and `GET /runs/{run_id}/trace` in `agentkit/api/routes.py`. Defined Pydantic boundary models in `agentkit/api/schemas.py`. Created dependency injection providers in `agentkit/api/deps.py` and database session utilities in `agentkit/db/session.py`. Integrated automatic run persistence across completion and failure paths in `Agent.run`. Added `RunNotFoundError` mapped to 404. Integration tests in `tests/integration/test_api_runs.py`.

### M7-T04: Discovery & Health Routes
- **Status**: Done
- **PR**: #29
- **Commits**: 2
- **Notes**: Implemented `GET /tools` returning list of registered tools with introspected JSON schemas (protected by `X-API-Key`). Implemented `GET /health` with live `SELECT 1` database query and Redis client ping (publicly accessible). Added `discovery_router` in `agentkit/api/routes.py` and wired into `create_app`. Integration tests in `tests/integration/test_api_health.py`.

### M8-T01: OpenAI SDK Adapter
- **Status**: Done
- **PR**: #30
- **Commits**: 2
- **Notes**: Implemented `OpenAIClient` adapter in `agentkit/llm/openai.py` using official `openai` SDK. Supports message translation, function schema mapping, tool call argument parsing, token usage extraction, and domain exception mapping (`AuthenticationError`, `RateLimitExceededError`, `LLMProviderError`). Unit tests in `tests/unit/test_llm_openai.py`.

### M8-T02: Provider Factory
- **Status**: Done
- **PR**: #31
- **Commits**: 2
- **Notes**: Implemented `create_llm_client` and `create_llm_client_from_settings` in `agentkit/llm/factory.py` dynamically provisioning `GeminiClient`, `OpenAIClient`, or `FakeLLMClient` with case normalization and provider validation. Integrated into FastAPI `get_llm_client` dependency in `agentkit/api/deps.py`. Unit tests in `tests/unit/test_llm_factory.py`.

### M9-T01: LLM Exponential Backoff Retry with Jitter
- **Status**: Done
- **PR**: #32
- **Commits**: 2
- **Notes**: Implemented async exponential backoff retry decorator and `RetryingLLMClient` wrapper in `agentkit/llm/retry.py`. Accurately distinguishes transient errors (`RateLimitExceededError`, `TimeoutError`, `ConnectionError`, `LLMProviderError` for 429 and 5xx) from non-transient errors (4xx, `AuthenticationError`, `ValueError`) which fail immediately. Unit tests in `tests/unit/test_llm_retry.py`.

### M9-T02: Redis-Backed Sliding Window Rate Limiter
- **Status**: Done
- **PR**: #33
- **Commits**: 2
- **Notes**: Implemented sliding window `RateLimiter` dependency in `agentkit/api/ratelimit.py` backed by Redis Sorted Sets (ZSET) with microsecond precision and an in-memory fallback. Returns HTTP 429 with computed `Retry-After` header when limit is exceeded. Wired into `/runs` and `/tools` endpoints. Unit tests in `tests/unit/test_api_ratelimit.py`.

### M9-T03: Step and Run Timeout Verification
- **Status**: Done
- **PR**: #34
- **Commits**: 2
- **Notes**: Implemented end-to-end integration tests in `tests/integration/test_timeouts.py` verifying that hanging tools exceeding `tool_timeout_s` fail gracefully without aborting the agent, allowing the LLM to recover. Verified that hanging runs trigger `RunStatus.TIMED_OUT` with proper database persistence, that `raise_on_failure=True` raises `RunTimeoutError`, and that tool timeouts are recorded in `PostgresTraceSink`.

### M10-T01: Tool Failure Self-Correction Integration Test
- **Status**: Done
- **PR**: #35
- **Commits**: 2
- **Notes**: Implemented integration tests in `tests/integration/test_recovery.py` verifying that when a tool call fails with a computational error (calculator division by zero) or a file system error (sandboxed file not found), the error message is preserved in the conversation history as a tool response turn and the model successfully self-corrects its arguments on subsequent turns.

### M10-T02: Prompt Injection Resistance Test
- **Status**: Done
- **PR**: #36
- **Commits**: 2
- **Notes**: Implemented integration tests in `tests/integration/test_injection.py` verifying that prompt injection attack payloads in tool outputs and error messages are strictly quarantined within `Role.TOOL` message turns and cannot overwrite system instructions or hijack the agent's behavior. Updated `agentkit/tools/executor.py` to handle direct `ToolResult` returns with `model_copy`.

### M10-T03: Comprehensive Built-in Tools Security Suite
- **Status**: Done
- **PR**: #37
- **Commits**: 2
- **Notes**: Implemented exhaustive unit security test suite in `tests/unit/test_security_tools.py` covering AST evasion (blocking `eval`, `exec`, `open`, `__import__`, subclasses, lambda, comprehensions), SQL injection defenses (blocking DDL, DML, multi-statement queries, comments, PRAGMA/VACUUM), path traversal and symlink escapes in sandboxed file reader, and SSRF defenses (blocking loopbacks, RFC 1918 subnets, cloud metadata endpoints, non-HTTP schemes).


### M11-T01: Multi-Stage Production Dockerfile
- **Status**: Done
- **PR**: #38
- **Commits**: 2
- **Notes**: Created `.dockerignore` to exclude local caches and virtual environments, and multi-stage `Dockerfile` with `python:3.12-slim` builder and runtime stages. Configured non-root system user `agentkit` (UID 1000), sandboxed file directory `/tmp/agentkit_sandbox`, healthcheck instruction probing `/health`, and uvicorn application entrypoint.

### M11-T02: Docker Compose Environment
- **Status**: Done
- **PR**: #39
- **Commits**: 2
- **Notes**: Created production `docker-compose.yml` declaring `postgres` (PostgreSQL 16 Alpine), `redis` (Redis 7 Alpine), and `api` services with volume persistence, networks, resource limits, healthchecks (`pg_isready`, `redis-cli ping`, `curl /health`), dependency conditions (`condition: service_healthy`), automated Alembic database migrations on boot (`alembic upgrade head && uvicorn ...`), and updated `.env.example` with compose networking defaults. Verified compose syntax with `docker compose config`.

### M11-T03: GitHub Actions CI Pipeline
- **Status**: Done
- **PR**: #40
- **Commits**: 2
- **Notes**: Created `.github/workflows/ci.yml` featuring two decoupled jobs with concurrency cancellation. `lint` runs on Python 3.12 executing `ruff check`, `ruff format --check`, and `mypy agentkit tests`. `test` runs across a Python matrix (`3.11`, `3.12`) with `postgres:16-alpine` and `redis:7-alpine` service containers with integrated healthchecks, runs database migrations via `alembic upgrade head`, and verifies code coverage with `pytest --cov=agentkit --cov-fail-under=85` and XML artifact uploads.

### M12-T01: Demo Database Seed & Multi-Tool End-to-End Demo
- **Status**: Done
- **PR**: #41
- **Commits**: 2
- **Notes**: Implemented `scripts/seed_demo_db.sql` populating `products` and `orders` tables with ANSI-standard SQL. Created `examples/demo.py` showcasing an autonomous agent solving a multi-step analytical problem by querying the database using `sql_readonly` and calculating revenue with sales tax via `calculator`. Supported deterministic offline execution using `FakeLLMClient` alongside live providers. Added integration tests in `tests/integration/test_demo.py`.

### M12-T02: AWS Deployment Guide
- **Status**: Done
- **PR**: #42
- **Commits**: 2
- **Notes**: Authored comprehensive single-node AWS EC2 production deployment guide in `docs/DEPLOY.md`. Covered architecture diagrams, EC2 hardware sizing recommendations, strict Security Group isolation rules (prohibiting public database/redis ingress), host Docker CE setup, dynamic AWS SSM Parameter Store secrets retrieval script, production systemd daemon unit (`agentkit.service`), automated TLS reverse proxy configurations for both Caddy and Nginx + Certbot, post-deployment health verification, and daily S3 database backup crons.

### M12-T03: Comprehensive Portfolio README
- **Status**: Done
- **PR**: #43
- **Commits**: 2
- **Notes**: Authored complete portfolio `README.md` with CI badge roster, architecture Mermaid flowchart illustrating the ReAct loop and dual-persistence layer, quickstart guide with Docker Compose and local setup, 10-line `@tool` creation tutorial with automatic schema introspection, third-party LLM provider extension guide (`AnthropicClient`), sample `StepTrace` JSON telemetry output, ADRs summary (ADR-001 through ADR-004), AWS production deployment references, testing guides, current limitations, and future roadmap.

### M13-T01: Real-Infrastructure Test Tier
- **Status**: Done
- **PR**: #44
- **Commits**: 1
- **Notes**: Added `tests/conftest.py` providing opt-in live fixtures and `tests/real/` covering `PostgresMemoryStore`, `PostgresTraceSink`, `RedisMemoryStore`, the rate limiter and a full-lifespan API round trip. The suite applies Alembic migrations through the real PostgreSQL dialect, verifies native `uuid`/`json`/`timestamptz` round trips, `ON DELETE CASCADE`, FK rejection of orphan steps, genuine Redis TTL expiry and the hiredis codec. Skips unless `AGENTKIT_REAL_INFRA=1`; truncates `runs`/`steps` and deletes only `session:*` / `ratelimit:*` keys.

### M13-T02: Atomic Sliding-Window Rate Limiting
- **Status**: Done
- **PR**: #44
- **Commits**: 1
- **Notes**: A concurrency test against live Redis revealed the limiter admitted 19 of 20 simultaneous requests against a limit of 5, because `ZCARD` was read and `ZADD` written in separate round trips with an `await` between them. Replaced with a Lua script performing trim, count and insert as one indivisible operation, retaining a non-atomic fallback only for deployments that forbid scripting. Added `lupa` as a dev dependency so fakeredis exercises the same script path as production.

### M13-T03: CI Split, Compose Hardening and Documentation Corrections
- **Status**: Done
- **PR**: #44
- **Commits**: 1
- **Notes**: Split CI into a hermetic Python matrix job and a `Test Against Live PostgreSQL and Redis` job that runs the `real_infra` tier against real service containers. Fixed `get_db_session`, which returned without yielding when no session factory was configured and would have surfaced as an opaque `generator didn't yield` RuntimeError; it now raises `ServiceUnavailableError` mapped to HTTP 503, and `lifespan` reuses the `agentkit.db.session` helpers instead of duplicating engine construction. `docker-compose.yml` no longer defaults `API_KEYS` to a known test key. `examples/demo.py` now persists run and step records to real PostgreSQL and Redis by default. Corrected the README and this file's earlier claims that CI exercised the database and cache.

### M13-T04: Gemini Tool Calling & Active Model Standard
- **Status**: Done
- **PR**: #45
- **Commits**: 2
- **Notes**: Resolved Gemini 2.x/3.x tool-calling incompatibility. Added `thought_signature: bytes | None = None` to neutral `ToolCall` model; captured candidate `thought_signature` from Gemini responses and forwarded it on subsequent conversation turns via `types.Part(function_call=..., thought_signature=...)`; resolved `Role.TOOL` function response names using assistant tool call history with `role="tool"`; standardized active default model to `gemini-2.0-flash` across application config, factory, `.env.example`, and `docker-compose.yml`; ensured `RedisMemoryStore` serializes messages with `mode="json"` for safe base64 bytes encoding.

### M13-T05: Idempotent Demo Seeding & Docker Compose Auto-Seeding
- **Status**: Done
- **PR**: #46
- **Commits**: 2
- **Notes**: Completed SPEC Section 13 requirement for automated demo database seeding on `docker compose up`. Updated `scripts/seed_demo_db.sql` with `ON CONFLICT (id) DO NOTHING;` to guarantee idempotency across multiple runs; created standalone CLI and programmatic async seeding script `scripts/seed.py`; updated `Dockerfile` to copy `scripts/` to runtime container; updated `docker-compose.yml` migrations service to execute `alembic upgrade head && python scripts/seed.py`; delegated `examples/demo.py` seeding to `scripts.seed.seed_database`; added integration test in `tests/integration/test_seed.py`.


### M13-T06: Whole-Project Audit Remediation
- **Status**: Done
- **PR**: #47
- **Commits**: 1
- **Notes**: Remediated 17 findings from a full-codebase audit. Critical: removed the `ak_test_key_12345` default from `Settings.API_KEYS` and made the field required; dropped the `FakeLLMClient` fallback from `get_llm_client` in favour of `ServiceUnavailableError` (503); rebuilt `Agent` per request instead of memoizing the first assembly on `app.state`, which had pinned every later request to the first request's LLM client, registry, memory store and trace sink; wired the previously unwired `RetryingLLMClient` into the request path with new `LLM_MAX_RETRIES`/`LLM_RETRY_BASE_DELAY_S`/`LLM_RETRY_MAX_DELAY_S` settings; forwarded `get_run_trace`/`list_runs` through `TieredMemoryStore`; moved `http_fetch` DNS off the event loop with `asyncio.to_thread`, added a post-connect `server_addr` check against the validated addresses to close the DNS-rebinding TOCTOU window, and replaced full-body buffering with a byte-budgeted stream; replaced the rate limiter's non-atomic fallback with `WATCH`/`MULTI`/`EXEC`. Moderate: fixed the `RedisMemoryStore.truncate_messages` off-by-one that returned `max_messages + 1` entries when a system prompt was present; switched generated `tool_call_id`s to full UUIDs; wrapped response parsing in both LLM adapters inside the error boundary so malformed responses and malformed tool-argument JSON raise `LLMProviderError`; guarded `RetryingLLMClient.__getattr__` against recursion on a partially constructed instance; sanitized `/health` details so the unauthenticated endpoint no longer echoes driver error strings; made `sql_readonly` mask string literals and quoted identifiers before its keyword scan, allow `#>`/`#>>`/`#-`, apply the row limit in SQL and push `statement_timeout` to PostgreSQL. Minor: stopped logging raw run goals at INFO (length only, content at DEBUG); replaced `split(";")` in `scripts/seed.py` with a statement-aware splitter handling literals, comments and dollar-quotes; bounded calculator exponentiation by result bit length. Added `tests/unit/test_audit_regressions.py` (32 tests) pinning each fix. Recorded in ADR-008, ADR-009, ADR-010. Suite: 277 passed, 24 skipped; coverage 93% on core/tools/llm; `ruff check`, `ruff format --check` and `mypy agentkit` all clean.
