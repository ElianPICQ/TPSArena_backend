# TPSArena backend

Backend for **TPSArena**, a third-person free-for-all multiplayer shooter made with Godot 4.
Players form parties of up to 8 and launch matches on dedicated game servers. Strangers can then
join a running match from a public server list. Around that, the backend provides accounts,
friends, chat, presence, stats and leaderboards.

The backend is a set of microservices that talk over HTTP and Redis Streams. The Godot client
and the dedicated game server are not in this repository yet.

The full design is in [`project-context.md`](project-context.md), and open gaps are in [`TODO.md`](TODO.md).

## Services

| Service | Stack | Port | What it does |
|---|---|---|---|
| [BFF](services/bff) | Python, FastAPI | 8000 | Single entry point for the game client: forwards to the services and adds usernames |
| [Auth](services/auth) | Python, FastAPI | 8001 | Accounts, email codes, login, JWT + refresh tokens, password reset, account deletion, JWKS |
| [Server List](services/serverlist) | Python, FastAPI | 8002 | Game server registry, public server list, server allocation for parties, join tickets |
| [Social](services/social) | Python, FastAPI | 8003 | Profiles, friends, parties, invites, party launch |
| [Stats](services/stats) | Python, FastAPI | 8004 | Match results and history, XP, levels, achievements, leaderboards |
| [Real-time](services/realtime) | TypeScript, Node 24+ | 8005 | One WebSocket per player: presence, friend messages, party chat, live notifications ([protocol](services/realtime/PROTOCOL.md)) |

Shared pieces:
- **[`libs/common`](libs/common)** (`tpsarena-common`): JWT verification, event models, Redis Streams consumer, transactional outbox. Services pin a released git tag (`common-vX.Y.Z`).
- **PostgreSQL**: each service that needs it has its own database (Auth, Social, Stats, Real-time).
- **Redis**: one shared instance for the server registry, parties, presence, rate limits and the event streams. Each service has its own ACL user, limited to its own key prefix and the streams it uses.

```
Game client ──HTTPS──> BFF ──> Auth · Server List · Social · Stats
     │                                                     ▲
     └──WebSocket──> Real-time <──Redis Streams (events)───┘
Game server ──HTTPS──> Server List ; publishes match results to Redis Streams
```

## Running the full stack

Requirements: Docker with Compose, and an SSH key with access to this GitHub repository. Image
builds download `tpsarena-common` from it through your SSH agent.

```sh
cp .env.example .env                                         # development values work as-is
(cd services/auth && uv run python scripts/gen_keys.py)      # JWT signing key (git-ignored)
ssh-add                                                      # once per session
docker compose up -d --build
```

This starts 15 containers: the services and their workers, one PostgreSQL per service, Redis and
Mailpit. Outside the stack you reach:

- the BFF at http://localhost:8000 (OpenAPI docs at `/docs`);
- Real-time at `ws://localhost:8005/ws`;
- the Server List at http://localhost:8002, for game servers;
- caught emails at http://localhost:8025 (Mailpit).

Try it:

```sh
curl -X POST localhost:8000/auth/register -H 'content-type: application/json' \
  -d '{"email": "alice@example.dev", "username": "alice", "password": "secret123"}'
# Read the 6-digit code at http://localhost:8025, then:
curl -X POST localhost:8000/auth/verify-email -H 'content-type: application/json' \
  -d '{"email": "alice@example.dev", "code": "123456"}'     # returns the access and refresh tokens
```

To exercise every endpoint at once, open [`bruno/`](bruno) in [Bruno](https://www.usebruno.com/) and run the
collection with the `Local` environment, or run `npx @usebruno/cli run --env Local` from that folder.
It creates two players, reads their codes from Mailpit, goes through friends, a party launch, a
match and stats, and deletes both accounts at the end.

To read the tables, see [`DATABASES.md`](DATABASES.md).

Each service can also run alone, with its own database and Redis, from its own folder. Run
`docker compose --profile standalone up --build` there. Its README has the details.

## Development

The Python services use [uv](https://docs.astral.sh/uv/) and Python 3.13+. Real-time uses Node 24+.
Tests start their own throwaway PostgreSQL and Redis containers, so only Docker needs to be running.

```sh
cd services/<name> && uv sync && uv run pytest     # auth, serverlist, social, stats, bff
cd services/realtime && npm install && npm test
cd libs/common && uv run pytest
```

- **Shared package changes**: write them under "Unreleased" in `libs/common/CHANGELOG.md`, then release with
  `python scripts/release_common.py X.Y.Z`. That runs the tests, tags and pushes. Each service
  then upgrades by changing its tag and running `uv lock`. Real-time copies the event formats with
  `npm run sync:events -- X.Y.Z`.
- **Event formats** have one source: the Pydantic models in `libs/common`. They are exported as
  JSON Schema, and Real-time generates its TypeScript types from that file.
- **Secrets**: `ENVIRONMENT=development` allows the `change-me-dev-*` defaults. Any other value
  makes the services refuse to start until real secrets of at least 32 characters are set.
- **Platforms**: development happens on Debian and on Windows 11 (Docker Desktop + WSL 2). Keep LF line endings and
  write helper scripts in Python.

Conventions for working in the code are in [`CLAUDE.md`](CLAUDE.md).

## Status

- **Built:** every backend service in the design.
- **Not built yet:**
  - the Godot client and dedicated game server;
  - CI;
  - HTTPS / `wss`;
  - deployment;
  - a pool of idle game servers that party launches can use.
- **Before a public release:** move email sending to your own domain and a transactional provider (see [`TODO.md`](TODO.md)).
