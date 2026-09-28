# AgentKit

AgentKit is a lightweight, provider-agnostic ReAct agent framework in Python built from scratch without external agent frameworks (such as LangChain or LlamaIndex). It provides a production-grade ReAct reasoning loop, automated Pydantic-based tool schema generation, pluggable LLM adapters (Google Gemini and OpenAI), session caching in Redis, and durable execution tracing in PostgreSQL, exposed via a FastAPI REST service.

> **Status**: Project scaffolding and warm-up phase complete. See [docs/PLAN.md](docs/PLAN.md) for the implementation roadmap and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the architecture design.

## Documentation
- [Agent Specifications](docs/SPEC.md)
- [Architecture & Data Flow](docs/ARCHITECTURE.md)
- [Agent & Engineering Guidelines](AGENTS.md)
- [Implementation Plan & Backlog](docs/PLAN.md)
- [Architecture Decision Log](docs/DECISIONS.md)
- [Progress Log](docs/PROGRESS.md)
