# Multi-stage Dockerfile for Research Platform

# Stage 1: Base dependencies
FROM python:3.14-slim as base

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Create app directory
WORKDIR /app

# Stage 2: Development environment
FROM base as development

# Copy dependency files
COPY pyproject.toml .
COPY README.md .

# Install all dependencies including dev
RUN uv pip install -e ".[dev]"

# Copy application code
COPY src/ ./src/
COPY tests/ ./tests/

# Expose ports
EXPOSE 8000

# Development command
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# Stage 3: Production build
FROM base as builder

# Copy dependency files
COPY pyproject.toml .
COPY README.md .

# Install production dependencies only
RUN uv pip install -e .

# Copy application code
COPY src/ ./src/

# Stage 4: Production runtime
FROM python:3.14-slim as production

# Create non-root user
RUN groupadd -r app && useradd -r -g app app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /app/src ./src

# Change ownership
RUN chown -R app:app /app

# Switch to non-root user
USER app

# Expose ports
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Production command
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]