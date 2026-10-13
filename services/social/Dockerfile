# Build context: the repository root (needs libs/common). See docker-compose.yml.
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/services/social/.venv/bin:$PATH"
WORKDIR /app/services/social

COPY libs/common /app/libs/common
COPY services/social/pyproject.toml services/social/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY services/social .
RUN uv sync --locked --no-dev

CMD ["sh", "-c", "alembic upgrade head && uvicorn social.main:app --host 0.0.0.0 --port 8003"]
