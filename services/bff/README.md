# BFF

The game client's single HTTPS entry point: forwards to the services and adds player names from Auth.

Port 8000. Owns: nothing (stateless).

## Run it alone
```
cp .env.example .env   # optional: the defaults work for local development
docker compose up --build
```

## Tests
`uv run pytest` — other services are mocked.

## In the full stack
The root `docker-compose.yml` includes this folder's `compose.yaml` and supplies the shared Redis
(with this service's ACL user) and the other services' addresses from the root `.env`.
