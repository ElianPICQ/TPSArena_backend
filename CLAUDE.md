# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

TPSArena: a third-person free-for-all multiplayer shooter. The client and dedicated servers are Godot 4 (GDScript). The backend is microservices.

- **`project-context.md` is the design spec**: settled decisions on services, Redis keys, streams, flows and API outline. Read it before adding a service or endpoint, follow it unless the user says otherwise, and update it when a decision changes.
- **`TODO.md`** lists known gaps and deferred design issues. Remove an item when it is done, and add new gaps there instead of leaving them only in code comments.

Every backend service in the spec is implemented: `services/{auth,serverlist,social,stats,bff,realtime}` and the shared package `libs/common`. The Godot client and the dedicated game server don't exist yet. The game server's events (`player.match`, `match.finished`) are defined, and tests publish them by hand.

Development happens on both Debian and Windows 11 (Docker Desktop + WSL 2). Keep LF line endings (`.gitattributes`), avoid OS-specific paths, and write helper scripts in Python (see `scripts/`).

## Layout

| Path | What | Port |
|---|---|---|
| `services/auth` | Auth / Users API (FastAPI) + worker (code emails, outbox) | 8001 |
| `services/serverlist` | Game server registry, public list, party allocation, join tickets (FastAPI, Redis only) | 8002 |
| `services/social` | Profiles, friends, parties (FastAPI) + worker | 8003 |
| `services/stats` | Match results, history, progression, leaderboards (FastAPI) + worker | 8004 |
| `services/realtime` | WebSocket service: presence, chat, pushes (TypeScript, Node 24+). Client protocol: `PROTOCOL.md` | 8005 |
| `services/bff` | Client entry point (FastAPI): forwards to the services and merges usernames | 8000 |
| `libs/common` | `tpsarena_common`: JWT/JWKS verification, event models, stream consumer, outbox, JSON Schema export | — |
| `infra/` | Shared Redis with one ACL user per service (`infra/redis/start.sh`), full-stack compose overrides | 6379 |

Each service is almost standalone. It has its own `compose.yaml`, `Dockerfile`, `.env.example`, `README.md`, lockfile and tests. Services that use PostgreSQL each have their own database.

## Commands

The Python services use **uv** (Python ≥ 3.13). Run these from inside the service directory:

```sh
uv sync                                   # install deps incl. dev group
uv run pytest                             # all tests (starts its own Postgres/Redis containers: Docker must run)
uv run pytest tests/test_routes.py::test_register_duplicates   # single test
uv run alembic revision --autogenerate -m "..."                # new migration (auth, social, stats)
docker compose --profile standalone up --build                 # the service alone, with its own Postgres + Redis
uv run --with-editable ../../libs/common pytest                # try an unreleased libs/common change
```

Real-time (`services/realtime`):

```sh
npm install
npm test                         # regenerates event types, then vitest (testcontainers)
npm run typecheck
npm run sync:events -- X.Y.Z     # copy the event contract from tag common-vX.Y.Z
```

From the repository root:

```sh
cp .env.example .env
(cd services/auth && uv run python scripts/gen_keys.py)    # JWT signing key -> services/auth/keys/ (git-ignored)
ssh-add                                                    # image builds fetch tpsarena-common over SSH
docker compose up -d --build                               # full stack
python scripts/release_common.py X.Y.Z [--dry-run]         # release libs/common (clean tree, runs its tests, tags, pushes)
```

- **Ports:** only the BFF (8000), the Server List (8002, game servers) and Real-time (8005, WebSocket) are meant for outside callers; the other published ports are for development. Mailpit inbox: http://localhost:8025.
- **Redis ACL changes:** after changing `infra/redis/start.sh` or its passwords, run `docker compose restart redis`. The ACL file is written only at container start.

## Shared package and event contract

- **Pinned by tag:** services depend on a **git tag** of `libs/common` (`common-vX.Y.Z`) from the private GitHub repo, pinned in `[tool.uv.sources]` and `uv.lock`. Add changes under "Unreleased" in `libs/common/CHANGELOG.md`, then release with `scripts/release_common.py`. To upgrade a service, change its tag and run `uv lock`. The release script needs a clean working tree.
- **Event formats:** they are defined once, as Pydantic models in `tpsarena_common/events.py`, and decorated with their stream and type. After changing them:
  1. Run `uv run python -m tpsarena_common.export_schemas` in `libs/common` (a test fails if `schemas/events.json` is stale).
  2. Release the package.
  3. Run `npm run sync:events` in Real-time, which generates its types and validates incoming events against the schema with Ajv.
- **Stream entries** have exactly `event_id`, `type` and `data` (JSON).

## Architecture conventions (as implemented)

- **PostgreSQL:** each service has its own database. Inside it, tables live in a schema named after the service (`MetaData(schema=...)`). Alembic's `env.py` creates that schema and keeps its version table there. Containers apply migrations at startup. Real-time uses plain SQL files in `migrations/`.
- **Redis:** one shared instance with AOF persistence on.
  - Every key starts with its service's prefix (`auth:`, `serverlist:`, `social:`, `stats:`, `realtime:`). Only stream names are shared.
  - Each service has an ACL user: its own prefix, read/write on the streams it consumes, write-only on the streams it only publishes, and `-@dangerous`. Lua scripts must receive every key through `KEYS`, and those keys need read/write permission.
  - The `default` user must keep full rights, or the AOF replay drops transactions.
  - Service clients don't call `INFO`.
- **Streams:**
  - **Consumers:** they run in a separate worker process (`python -m <svc>.worker`; Real-time consumes in-process). They use `StreamConsumer` from `libs/common` (TypeScript twin: `services/realtime/src/streams.ts`), with these rules:
    - One consumer group per consuming service.
    - `XACK` after handling, and `XAUTOCLAIM` for stale entries.
    - Skip already-handled events using `{prefix}:handled:{group}:{event_id}` keys.
    - Acknowledge and drop events that can't be decoded.
    - Survive Redis outages.
  - **Publishers:**
    - An event about a PostgreSQL change goes through the service's **outbox**: `await outbox.add(session, event)` before the commit, then `await outbox.try_flush()` after it. Workers publish whatever is left.
    - An event about Redis-only state is published directly, or from inside the Lua script that makes the change (Server List).
  - The two TypeScript twins must keep behaving like the Python originals: `jwt.ts` (same error codes) and `streams.ts`.
- **Errors:** services raise `APIError(status, error_code, message)`, which is rendered as `{"error": ..., "message": ...}`. The game client branches on the `error` codes, so they are API contract. The same goes for WebSocket close codes and reasons (see `PROTOCOL.md`).
- **BFF:**
  - It keeps one `httpx.AsyncClient` per upstream on `app.state` and forwards bodies and status codes unchanged, because the upstream owns validation.
  - It sets `X-Forwarded-For`, which Auth trusts for per-IP rate limits. That is safe only because Auth isn't exposed.
  - Upstream down → `503`, non-JSON reply → `502`.
  - It adds usernames to Social and Stats responses with one batch lookup (`bff/names.py`).
- **Auth security choices:**
  - JWTs are EdDSA (Ed25519) with a `kid`, `sub` = user UUID, and a 15-minute lifetime. Other services verify them through Auth's JWKS.
  - Refresh tokens are stored hashed and rotated. Only a *rotated* token used again counts as theft and revokes every session.
  - Revoking every session (password reset, theft) publishes `auth.sessions_revoked`.
  - Passwords use Argon2 (pwdlib). 6-digit email codes are stored as HMAC-SHA256.
  - Responses must not reveal whether an email is registered.
- **Service-to-service authentication:**
  - Social → Server List uses a shared service secret.
  - Game servers register with `GAME_SERVER_SECRET` and then use their own `server_token`.
- **Tests:** Python tests start testcontainers when `tests/conftest.py` is imported, before the app is imported, because settings and clients are created at import time. BFF tests mock the upstreams with `respx`. The test Redis has **no ACLs**, so check permission changes against the full stack.
- Everything is async: SQLAlchemy async + asyncpg, `redis.asyncio`, `httpx`, `aiosmtplib`. Settings come from `pydantic-settings` (`get_settings()`, cached with `lru_cache`).
- **SMTP** is configured only through env (`SMTP_*`): Mailpit locally, a Gmail app password for the alpha.
