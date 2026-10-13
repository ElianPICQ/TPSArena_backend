# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

TPSArena: a third-person free-for-all multiplayer shooter. The client and dedicated servers are Godot 4 (GDScript). The backend is microservices. **`project-context.md` is the design spec**: settled decisions on services, Redis keys, streams, flows and API outline. Read it before adding a service or endpoint, and follow it unless the user says otherwise.

Implemented so far (the rest of the spec is not built yet):
- `services/auth`: Auth / Users API (FastAPI). Register, verify-email (+ resend), login, refresh, logout, password reset. The worker (`auth.worker`) sends emails. **Not yet built:** `GET /me`, `DELETE /me`, `GET /users?ids=`, JWKS, the cleanup of unverified accounts, the `user.deleted` event.
- `services/bff`: BFF (FastAPI). It forwards only `/auth/*` to Auth for now.
- No Godot project, Server List, Social, Stats or Real-time service, and no shared internal Python package yet.

Development happens on both Debian and Windows 11 (Docker Desktop + WSL 2). Keep LF line endings (`.gitattributes`), avoid OS-specific paths, and write helper scripts in Python (see `scripts/`).

## Commands

Python services use **uv** (Python ≥ 3.13). Run these from inside the service directory (`services/auth` or `services/bff`):

```sh
uv sync                                   # install deps incl. dev group
uv run pytest                             # all tests
uv run pytest tests/test_routes.py::test_register_duplicates   # single test
uv run uvicorn auth.main:app --port 8001 --reload   # auth locally (bff: bff.main:app --port 8000)
uv run python -m auth.worker              # auth email worker
uv run alembic upgrade head               # apply auth migrations
uv run alembic revision --autogenerate -m "..."      # new auth migration
```

From the repository root:

```sh
cp .env.example .env
uv run --with cryptography python scripts/gen_keys.py   # Ed25519 JWT keys -> keys/ (gitignored)
docker compose up -d --build                             # everything
docker compose up -d postgres redis mailpit              # infra only, for running services/tests on the host
```

Mailpit inbox: http://localhost:8025. Only the BFF (`:8000`) is published; Auth (`:8001`) is reachable only inside the compose network.

Tests:
- `auth/tests/test_routes.py` are **integration tests**. They need Postgres and Redis from compose with migrations applied. They read verification codes back from the `auth.emails` stream instead of the mailbox. Each test uses a random `X-Forwarded-For` so the per-IP rate limits don't collide.
- `auth/tests/test_security.py` needs `keys/` to exist.
- BFF tests mock Auth with `respx` and need no infrastructure.
- Host defaults in each service's `config.py` point to `localhost` and to `../../keys/`. That is why commands must run from the service directory.

## Architecture conventions (as implemented)

- **Postgres**: one database (`tpsarena`), **one schema per service**, each with its own role restricted to that schema. Roles and schemas are created by `infra/postgres/init.sh`, which runs **only when the volume is first created**. When adding a service, extend it and recreate the volume, or create the role by hand. The SQLAlchemy `Base` sets `MetaData(schema="auth")`, and Alembic keeps its version table in that schema and only looks at that schema.
- **Redis** runs with AOF on. It is used for rate limits (`ratelimit:{action}:{key}`, INCR + EXPIRE NX) and as the message broker via **Redis Streams**.
- **Stream consumers are separate processes** from the API, with the same image and a different command (`python -m <svc>.worker`, see `auth-worker` in compose). Consumer pattern (`auth/worker.py`): consumer group per consuming service, `XREADGROUP` + `XACK`, periodic `XAUTOCLAIM` of stale pending messages, idempotency via a `handled:<group>:<event_id>` key. Producers publish with `MAXLEN ~` and an `event_id`. This logic is meant to move into a shared package once a second Python service consumes streams.
- **Errors**: services raise `APIError(status, error_code, message)`, which is rendered as `{"error": ..., "message": ...}`. The game client branches on `error` codes (e.g. `email_not_verified`, `invalid_code`, `rate_limited`), so treat them as API contract.
- **BFF** keeps one `httpx.AsyncClient` per upstream on `app.state` (created in `lifespan`). It forwards bodies and status codes unchanged, because the upstream owns validation. It sets `X-Forwarded-For` to the player's IP, which Auth trusts for per-IP rate limits (safe only because Auth isn't exposed). Upstream down → `503`, non-JSON reply → `502`.
- **Auth security choices**: JWTs are EdDSA (Ed25519) with `kid`, `sub` = user UUID, and a 15-minute lifetime. Refresh tokens are stored hashed and rotated. Passwords use Argon2 (pwdlib). 6-digit email codes are stored as HMAC-SHA256 with `CODE_SECRET`. Responses must not reveal whether an email is registered: resend and reset always give the same answer, and `email_not_verified` is returned only after a correct password.
- Everything is async: async DB (asyncpg), `redis.asyncio`, `httpx`, `aiosmtplib`. Settings come from `pydantic-settings` (`get_settings()`, cached with `lru_cache`).
- SMTP is configured only through env (`SMTP_*`): Mailpit locally, a Gmail app password for the alpha.
