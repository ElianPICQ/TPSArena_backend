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
