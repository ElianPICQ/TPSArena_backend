# Stats API

Match results, match history, player stats, progression (XP, levels, achievements), leaderboards. A worker (`python -m stats.worker`) records `match.finished`, handles `user.deleted` and publishes progress events left in the outbox.

Port 8004. Owns: PostgreSQL database `stats`, Redis keys `stats:*`.

## Run it alone
```
cp .env.example .env   # optional: the defaults work for local development
docker compose --profile standalone up --build
```
Player routes need tokens from Auth: when running alone, Auth is expected on the host (`AUTH_JWKS_URL`).
Building the image downloads `tpsarena-common` from the private GitHub repo through your SSH
agent: run `ssh-add` once per session if `ssh-add -l` lists no key.

## Tests
`uv run pytest` — starts throwaway PostgreSQL/Redis containers (Docker must be running), nothing else needed.

## In the full stack
The root `docker-compose.yml` includes this folder's `compose.yaml` and supplies the shared Redis
(with this service's ACL user) and the other services' addresses from the root `.env`.

## Upgrading tpsarena-common
The shared package comes from a git tag of this repo (`[tool.uv.sources]` in `pyproject.toml`).
1. Change `tag = "common-vX.Y.Z"` to the new release (see `libs/common/CHANGELOG.md`).
2. `uv lock`, then run the tests.

To try an unreleased change of `libs/common` without releasing it:
`uv run --with-editable ../../libs/common pytest`.
