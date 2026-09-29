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
Launch the API, PostgreSQL, and Redis together with automatic migrations:
```bash
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
# Completely offline demo (using FakeLLMClient - no API keys required!)
python examples/demo.py --offline

# Or live with Gemini:
python examples/demo.py --provider gemini
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
