# Build context: the repository root (needs libs/common). See docker-compose.yml.
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/services/serverlist/.venv/bin:$PATH"
WORKDIR /app/services/serverlist

COPY libs/common /app/libs/common
COPY services/serverlist/pyproject.toml services/serverlist/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY services/serverlist .
RUN uv sync --locked --no-dev

CMD ["uvicorn", "serverlist.main:app", "--host", "0.0.0.0", "--port", "8002"]
