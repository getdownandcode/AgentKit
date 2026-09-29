# Stage 1: Build stage
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc python3-dev && \
    rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy package definition and install production dependencies
COPY pyproject.toml README.md ./
COPY agentkit ./agentkit

RUN pip install --no-cache-dir --upgrade pip build wheel setuptools && \
    pip install --no-cache-dir .

# Stage 2: Production runtime stage
FROM python:3.12-slim AS runtime

# Install curl for healthcheck
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Create non-root system user and group
RUN groupadd -r -g 1000 agentkit && \
    useradd -r -u 1000 -g agentkit -s /bin/bash -m -d /app agentkit

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FILE_TOOL_BASE_DIR="/tmp/agentkit_sandbox"

# Create sandbox directory and set permissions
RUN mkdir -p /tmp/agentkit_sandbox && \
    chown -R agentkit:agentkit /tmp/agentkit_sandbox

WORKDIR /app

# Copy application files and migrations
COPY --chown=agentkit:agentkit alembic.ini ./
COPY --chown=agentkit:agentkit migrations ./migrations
COPY --chown=agentkit:agentkit agentkit ./agentkit

# Switch to non-root user
USER agentkit

# Expose HTTP port
EXPOSE 8000

# Container healthcheck using public /health route
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Start FastAPI application with uvicorn
CMD ["uvicorn", "agentkit.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
