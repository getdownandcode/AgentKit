# AgentKit ⚡️

[![CI](https://github.com/getdownandcode/AgentKit/actions/workflows/ci.yml/badge.svg)](https://github.com/getdownandcode/AgentKit/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg)](https://fastapi.tiangolo.com)
[![Pydantic v2](https://img.shields.io/badge/Pydantic-v2-E92063.svg)](https://docs.pydantic.dev/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-blue.svg)](https://mypy-lang.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**AgentKit** is a lightweight, provider-agnostic, production-grade ReAct (Reasoning + Acting) autonomous agent framework written entirely in Python from scratch — **with zero external agent frameworks** (no LangChain, no LlamaIndex, no CrewAI).

Designed from first principles for reliability, auditability, and production deployment, AgentKit implements an explicit ReAct state machine, automated Pydantic v2 tool schema synthesis, multi-provider LLM adapters (Google Gemini and OpenAI), dual-store persistence (ephemeral Redis session caching + durable PostgreSQL step-level execution tracing), and a secure FastAPI service.

---

## Key Features

- **Built From Scratch**: Transparent, debuggable async ReAct reasoning loop. No hidden chains, no monkey-patched prompts.
- **Provider-Agnostic LLM Layer**: First-class support for **Google Gemini** (`google-genai`) and **OpenAI** (`openai`), with an isolated `FakeLLMClient` for deterministic offline testing.
- **Declarative Type-Safe Tools**: Register Python functions with the `@tool` decorator. Function signatures, type hints, and docstrings are introspected into strict JSON Schema and dynamic Pydantic argument validators.
- **Production Built-in Tools**:
  - `calculator`: AST-parsed mathematical evaluator with strict operator whitelisting and recursion/exponent limits.
  - `sql_readonly`: Read-only SQL executor with single-SELECT AST validation, comment stripping, and DDL/DML blocking.
  - `read_file`: Path traversal and symlink escape defended sandboxed file reader.
  - `http_fetch`: SSRF-protected HTTP client blocking private subnets, loopbacks, and cloud metadata endpoints.
  - `web_search`: Search provider adapter with result truncation.
- **Dual-Store Persistence**:
  - **Redis**: Sliding-window session memory and high-throughput sliding window rate limiter.
  - **PostgreSQL**: Durable execution audit trails with step-by-step latency, token counts, and tool outcomes.
- **Enterprise-Grade Reliability**:
  - Exponential backoff retry with full jitter for transient LLM errors.
  - Global run timeout, per-tool timeout guards, and consecutive duplicate tool call loop detection.
  - Structured contextual JSON logging (`run_id`, `step_no`, duration) with zero raw confidential prompt leakage.
- **Containerized & CI-Ready**: Multi-stage production `Dockerfile`, `docker-compose.yml` with healthchecks, and full GitHub Actions CI matrix (`ruff`, `mypy`, `pytest` >= 85% coverage).

---

## Architecture & Data Flow

```mermaid
flowchart TD
    User["Client / Caller"] -->|"HTTP POST /runs"| API["FastAPI Layer (agentkit/api)"]
    API -->|"Auth & Rate Limiting"| Middleware["Auth (X-API-Key) & Redis Rate Limiter"]
    Middleware -->|"Dispatches Run"| Core["ReAct Agent Runtime (agentkit/core)"]

    subgraph "Core ReAct Execution Loop"
        Core -->|"1. Format History & Tools"| LLM["LLM Client Adapter (agentkit/llm)"]
        LLM -->|"2. Model Reasoning"| Provider["Gemini / OpenAI / Fake"]
        Provider -->|"3. Tool Call Request"| Core
        Core -->|"4. Duplicate & Loop Check"| Guard["Guards & Circuit Breakers"]
        Guard -->|"5. Execute Tool"| Executor["Tool Registry (agentkit/tools)"]
        Executor -->|"6. Tool Output (ok/error)"| Core
        Core -->|"7. Append Result Turn"| LLM
    end

    subgraph "Persistence & Telemetry"
        Core -->|"Session Context"| RedisStore[("Redis (Session TTL)")]
        Core -->|"Step Traces & Run Records"| PGStore[("PostgreSQL (Durable Audit)")]
    end

    Core -->|"Final Synthesized Answer"| API
    API -->|"JSON Response"| User
```

### Layering Rules
AgentKit enforces a unidirectional dependency hierarchy:
$$\text{API Layer} \longrightarrow \text{Core Layer} \longrightarrow (\text{LLM}, \text{Tools}, \text{Memory}, \text{DB})$$
Lower layers never import from upper layers. Dependencies are passed explicitly via constructors and FastAPI dependency injection.

---

## Quickstart Guide

### 1. Prerequisites
- Python 3.11+ (Python 3.12 recommended)
- Docker & Docker Compose (optional for local containerized stack)
- Git

### 2. Installation
```bash
# Clone the repository
git clone https://github.com/getdownandcode/AgentKit.git
cd AgentKit

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install package with all runtime and development dependencies
pip install --upgrade pip
pip install -e ".[dev]"
```

### 3. Environment Configuration
Copy the sample environment file and set your credentials:
```bash
cp .env.example .env
```

Edit `.env`:
```ini
ENVIRONMENT=development
LOG_LEVEL=INFO

# LLM Providers (at least one key or use offline mode)
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key_here
OPENAI_API_KEY=your_openai_api_key_here

# Persistence
DATABASE_URL=postgresql+asyncpg://agentkit:agentkit_password@localhost:5432/agentkit
REDIS_URL=redis://localhost:6379/0

# Security
API_KEYS=dev_key_1,dev_key_2
JWT_SECRET=super_secret_jwt_key_at_least_32_bytes_long
```

### 4. Running with Docker Compose (Recommended)
Launch the API, PostgreSQL, and Redis together with automatic migrations.

`API_KEYS` has no default in `docker-compose.yml`: the stack refuses to start until you
supply one, so a known test key can never reach a reachable instance.

```bash
export API_KEYS=ak_local_dev_key   # or place API_KEYS in your .env
docker compose up -d --build

# Verify all services are healthy
docker compose ps
```

Check API health:
```bash
curl -s http://localhost:8000/health | jq .
```

### 5. Running the Multi-Tool Demo
AgentKit includes a complete multi-tool demo script that seeds a database and combines SQL analysis with mathematical calculation:

```bash
# Against real infrastructure (PostgreSQL + Redis from your .env).
# Run and step records are persisted exactly as they would be in production.
docker compose up -d postgres redis
alembic upgrade head
python examples/demo.py --provider gemini

# Or completely offline (FakeLLMClient + in-memory SQLite - no services, no API keys)
python examples/demo.py --offline
```

The demo prints the run ID; you can then inspect what it wrote:

```bash
curl -s -H "X-API-Key: $API_KEYS" "http://localhost:8000/runs/<run_id>/trace" | jq .
```

---

## 10-Line `@tool` Tutorial

Creating a custom tool in AgentKit requires only native Python typing and the `@tool` decorator. AgentKit automatically parses argument types, docstring parameter documentation, and registers JSON Schema validators.

```python
from agentkit.tools.registry import default_registry, tool


@tool
def currency_converter(amount: float, from_curr: str, to_curr: str) -> float:
    """Convert an amount from one fiat currency to another.

    Args:
        amount: Numerical money amount to convert.
        from_curr: ISO 3-letter source currency code (e.g. USD).
        to_curr: ISO 3-letter target currency code (e.g. EUR).
    """
    rates = {"USD": 1.0, "EUR": 0.92, "GBP": 0.79, "JPY": 155.0}
    amount_in_usd = amount / rates[from_curr.upper()]
    return round(amount_in_usd * rates[to_curr.upper()], 2)


# Schema is automatically generated and inspectable
schema = default_registry.get("currency_converter").schema
print(schema.parameters)
```

Tools can be synchronous or asynchronous (`async def`), and can be attached to custom scoped registries:
```python
from agentkit.tools.registry import ToolRegistry

isolated_registry = ToolRegistry()
isolated_registry.register(currency_converter)
```

---

## Provider Extension Guide

Adding a new LLM provider (such as Anthropic Claude, Mistral, or a local vLLM endpoint) is straightforward. Simply implement the `LLMClient` abstract interface:

```python
from collections.abc import Sequence
from agentkit.llm.base import LLMClient, LLMResponse, Message, TokenUsage
from agentkit.tools.models import ToolSchema


class AnthropicClient(LLMClient):
    """Custom Anthropic Claude adapter for AgentKit."""

    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-20241022") -> None:
        self.api_key = api_key
        self.model = model

    async def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema] | None = None,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        # 1. Translate neutral AgentKit Message sequence to Anthropic format
        # 2. Translate neutral ToolSchema to Anthropic tool definitions
        # 3. Call client API asynchronously
        # 4. Return neutral LLMResponse(text=..., tool_calls=[...], usage=...)
        ...
```

Register your client in `agentkit/llm/factory.py`:
```python
# In agentkit/llm/factory.py
if normalized_provider == "anthropic":
    return AnthropicClient(api_key=api_key or settings.ANTHROPIC_API_KEY, model=model)
```

---

## Sample Trace Output

Every reasoning step in AgentKit is captured as a structured `StepTrace` and recorded to the active `TraceSink` (such as `PostgresTraceSink` or `InMemoryTraceSink`):

```json
{
  "run_id": "8e3b52d4-1a93-4a1e-8e81-cf1b41db61e8",
  "step_number": 2,
  "thought": "SQL query returned total revenue of $3,200.00. Now calculating 8.5% sales tax.",
  "tool_calls": [
    {
      "id": "call_calc_4981",
      "name": "calculator",
      "arguments": {
        "expression": "3200 * 1.085"
      }
    }
  ],
  "tool_results": [
    {
      "tool_call_id": "call_calc_4981",
      "tool_name": "calculator",
      "ok": true,
      "output": "3472.0",
      "error": null,
      "duration_ms": 1
    }
  ],
  "input_tokens": 420,
  "output_tokens": 45,
  "step_duration_ms": 380,
  "timestamp": "2026-09-29T23:26:11.993000Z"
}
```

View run traces programmatically via REST API:
```bash
curl -H "X-API-Key: YOUR_API_KEY" http://localhost:8000/runs/{run_id}/trace | jq .
```

---

## Architectural Decisions (ADR Summary)

Key architectural trade-offs are documented in [docs/DECISIONS.md](docs/DECISIONS.md):

- **ADR-001: Framework-less Agent Runtime**: Built directly on Python standard libraries and async primitives to eliminate framework bloat, prevent hidden prompt modifications, and allow exact step debugging.
- **ADR-002: Neutral LLM Adapter Layer**: Pluggable `LLMClient` protocol ensures that changing model providers requires zero changes to the core reasoning loop.
- **ADR-003: Tiered Dual-Store Persistence**:
  - Redis manages ephemeral conversation sliding windows with automatic TTL expiration and rate limiting.
  - PostgreSQL retains permanent, queryable execution audits and performance metrics.
- **ADR-004: Declarative Tool Introspection**: Pydantic v2 runtime introspection automatically converts native Python type hints into JSON Schema and validates tool arguments before execution.

---

## Production AWS Deployment

AgentKit is designed to run reliably as a single-node containerized deployment on AWS EC2 behind a TLS reverse proxy (Caddy or Nginx) with automated database backups to S3.

Complete step-by-step instructions, including security group rules, systemd service daemon configuration, and AWS SSM secret retrieval, are available in the [AWS Deployment Guide](docs/DEPLOY.md).

---

## Testing & Quality Assurance

AgentKit enforces strict code quality and comprehensive test coverage across all layers:

```bash
# Run the complete hermetic test suite (no services required)
pytest -v

# Run with test coverage report (enforcing >=85% threshold)
pytest --cov=agentkit --cov-report=term-missing --cov-fail-under=85

# Run Ruff linter and formatter check
ruff check .
ruff format --check .

# Run Mypy strict type-checker
mypy agentkit tests examples
```

### Two test tiers

The default suite is hermetic: SQLite, `fakeredis` and `FakeLLMClient` stand in for
PostgreSQL, Redis and the LLM providers, so `pytest` runs in about two seconds with no
services. A second, opt-in tier runs the same components against **live** PostgreSQL and
Redis, covering behaviour the hermetic tier structurally cannot assert:

- native `uuid` / `json` / `timestamptz` columns and asyncpg parameter binding
- real foreign-key enforcement and `ON DELETE CASCADE` (SQLite ignores FKs by default)
- Alembic migrations applied through the real PostgreSQL dialect
- genuine Redis TTL expiry and the hiredis codec
- the Lua-backed atomic rate limiter under concurrency

```bash
# Start the services the tier needs
docker compose up -d postgres redis

# Run only the real-infrastructure suite
AGENTKIT_REAL_INFRA=1 pytest -m real_infra tests/real

# Or point it at any other PostgreSQL / Redis
AGENTKIT_REAL_INFRA=1 \
  DATABASE_TEST_URL=postgresql+asyncpg://user:pass@localhost:5432/agentkit_test \
  REDIS_TEST_URL=redis://localhost:6379/15 \
  pytest -m real_infra tests/real
```

Without `AGENTKIT_REAL_INFRA=1` these tests skip, so the default `pytest` invocation stays
hermetic. The suite truncates the `runs` and `steps` tables and deletes only
`session:*` / `ratelimit:*` Redis keys, so point it at a dedicated test database.

### CI

All pull requests trigger GitHub Actions with three jobs: `Lint & Type Check`; a hermetic
`Test Hermetic` matrix across Python 3.11 and 3.12 with coverage gating; and
`Test Against Live PostgreSQL and Redis`, which runs the `real_infra` tier against real
PostgreSQL 16 and Redis 7 service containers.

---

## Limitations & Future Roadmap

### Current Limitations
- **Sequential Tool Execution**: In the current ReAct loop, multiple tool calls in a single turn are executed sequentially rather than in parallel.
- **Single-Node API**: Rate limiting and session caching use a single Redis instance; distributed Redis cluster configurations are not yet automated.
- **Synchronous Responses**: The `/runs` REST endpoint returns when the full run completes; Server-Sent Events (SSE) streaming is not yet enabled.

### Future Roadmap
- [ ] **Parallel Tool Execution**: Dispatch independent tool calls concurrently using `asyncio.gather()`.
- [ ] **Streaming SSE Token Protocol**: Real-time streaming of thoughts, tool invocations, and token chunks over Server-Sent Events.
- [ ] **Multi-Agent Orchestration**: Hierarchical supervisor-worker agent delegation without external frameworks.
- [ ] **Human-in-the-Loop Interrupts**: Pausing execution for human approval before invoking sensitive tools (e.g., destructive actions or payment APIs).

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
