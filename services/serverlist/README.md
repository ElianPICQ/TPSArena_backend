# Server List API

Game server registry, public server list, party allocation, join tickets.

Port 8002. Owns: Redis keys `serverlist:*`.

## Run it alone
```
cp .env.example .env   # optional: the defaults work for local development
docker compose --profile standalone up --build
```
Player routes need tokens from Auth: when running alone, Auth is expected on the host (`AUTH_JWKS_URL`).
Building the image downloads `tpsarena-common` from the private GitHub repo through your SSH
agent: run `ssh-add` once per session if `ssh-add -l` lists no key.

## Game servers
`POST /servers` takes the shared `GAME_SERVER_SECRET` and returns the server's own `server_token`.
Every later call of that server (heartbeat, deregister, ticket verification) sends
`Authorization: Bearer <server_token>`; a server can't act as another one. A server that missed
its heartbeats for 15 s is removed (`404 server_not_found` on its next heartbeat: register again).

Events on `serverlist.events`, added by the Lua scripts themselves: `server.removed` (deregistered,
or removed by the sweeper that runs in each API instance) and `server.reservation_expired`.

## Tests
`uv run pytest` — starts a throwaway Redis container (Docker must be running), nothing else needed.

## In the full stack
The root `docker-compose.yml` includes this folder's `compose.yaml` and supplies the shared Redis
(with this service's ACL user) and the other services' addresses from the root `.env`.

## Upgrading tpsarena-common
The shared package comes from a git tag of this repo (`[tool.uv.sources]` in `pyproject.toml`).
1. Change `tag = "common-vX.Y.Z"` to the new release (see `libs/common/CHANGELOG.md`).
2. `uv lock`, then run the tests.

To try an unreleased change of `libs/common` without releasing it:
`uv run --with-editable ../../libs/common pytest`.
