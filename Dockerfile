FROM node:24-bookworm-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ENV VITE_REGISTRATION_REQUIRES_INVITE=true
RUN npm run build

FROM python:3.14-slim-bookworm AS python-build
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN uv sync --locked --no-dev --no-editable

FROM python:3.14-slim-bookworm AS runtime
WORKDIR /app
RUN useradd --create-home --uid 10001 app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AI_WORKSPACE_ENVIRONMENT=production \
    AI_WORKSPACE_DEBUG=false \
    AI_WORKSPACE_FRONTEND_DIST=/app/frontend/dist
COPY --from=python-build /app/.venv /app/.venv
COPY --from=frontend /frontend/dist /app/frontend/dist
COPY alembic.ini ./
COPY alembic/ ./alembic/
USER app
EXPOSE 8000
CMD ["sh", "-c", "exec python -m uvicorn ai_workspace.production:create_app --factory --host 0.0.0.0 --port \"${PORT:-8000}\" --timeout-graceful-shutdown 70"]
