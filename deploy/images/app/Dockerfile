# Build from the repo root: docker build -f deploy/images/app/Dockerfile --target app .
# One image for the Python services (sync, ingest-worker); the command is set per workload.
FROM python:3.14-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages packages
COPY services services

FROM base AS app
# Public, company-wide reading guides and glossary (read-only at run time).
COPY schema /app/schema
RUN uv sync --frozen --no-dev --package sync --package ingest-worker --package model-gateway
ENV PATH=/app/.venv/bin:$PATH
USER 65532:65532

# Dev only: runs in-cluster isolation tests (tests/leak/infra/in_cluster).
FROM base AS test-runner
RUN uv sync --frozen --all-packages
COPY tests tests
COPY scripts scripts
ENV PATH=/app/.venv/bin:$PATH
USER 65532:65532
