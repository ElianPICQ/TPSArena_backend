# Build from this folder: docker compose build (needs your SSH agent: tpsarena-common comes from the private GitHub repo).
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client && rm -rf /var/lib/apt/lists/*
# GitHub's published SSH host key (https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints).
RUN mkdir -p -m 0700 /root/.ssh \
    && echo "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl" > /root/.ssh/known_hosts

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=ssh uv sync --locked --no-dev --no-install-project
COPY . .
RUN --mount=type=ssh uv sync --locked --no-dev --no-editable

# Final image: the virtualenv and the code, without git or SSH.
FROM python:3.13-slim
WORKDIR /app
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH"

CMD ["sh", "-c", "alembic upgrade head && uvicorn auth.main:app --host 0.0.0.0 --port 8001"]
