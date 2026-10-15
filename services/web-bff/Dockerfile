FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY . .
RUN uv sync --locked --no-dev

# Only the site's nginx and the host's loopback reach this port (see compose.yaml), so the
# X-Forwarded-For it sets can be trusted: it carries the player's IP for Auth's rate limits.
CMD ["sh", "-c", "uvicorn web_bff.main:app --host 0.0.0.0 --port 8006 --proxy-headers --forwarded-allow-ips '*'"]
