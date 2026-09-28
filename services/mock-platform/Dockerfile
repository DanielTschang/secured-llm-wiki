# Build from the repo root: docker build -f services/mock-platform/Dockerfile .
FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages packages
COPY services services
RUN uv sync --frozen --no-dev --package mock-platform
# Synthetic test set only (development rule: no real data).
COPY tests/fixtures/synthetic_litho_testset /data/testset
ENV KC_MOCK_MANIFEST=/data/testset/manifest.json
USER 65532:65532
EXPOSE 8080
CMD ["/app/.venv/bin/uvicorn", "--factory", "mock_platform.app:create_app_from_env", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
