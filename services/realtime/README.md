# Real-time service

One WebSocket per online player: presence, friend messages, party chat, and pushing other
services' events (friend requests, party invites and updates, match tickets, level ups,
achievements). TypeScript (Node 24+): `ws`, `ioredis`, `jose`, `pg`.

Port 8005: WebSocket at `/ws` (game clients connect directly), plus `GET /presence` and
`GET /conversations/{friendId}/messages` (reached through the BFF). Protocol: [PROTOCOL.md](PROTOCOL.md).

Owns: PostgreSQL database `realtime` (schema `chat`: friend messages, kept 90 days), Redis keys and
Pub/Sub channels `realtime:*`. Consumes `social.events`, `stats.progress`, `player.match`,
`user.deleted`, `auth.sessions`, `serverlist.events`; publishes `presence.changed`.

## Run it alone
```
cp .env.example .env   # optional: the defaults work for local development
docker compose --profile standalone up --build
```
Tokens are checked with Auth's JWKS and friends come from Social: when running alone they are
expected on the host (`AUTH_JWKS_URL`, `SOCIAL_URL`).

## Tests
`npm test` — starts throwaway PostgreSQL/Redis containers (Docker must be running), a fake Social
and local tokens; runs real instances, two at once to check delivery between instances.

## Several instances
Each stream event is handled by one instance (consumer group `realtime`), which publishes the
pushes on the Pub/Sub channel `realtime:deliver`; every instance delivers them to its own sockets.
Presence transitions run in Lua scripts, so "online"/"offline" is announced once.

## Event contract
Event formats come from `tpsarena-common` (Python, the single source of truth), exported as JSON
Schema. `contracts/events.json` is a copy from a release tag (version in `contracts/VERSION`);
`npm run build`/`npm test` generate `src/generated/events.ts` from it, so a format change breaks
the TypeScript build. Upgrade: `npm run sync:events -- X.Y.Z`, then build and test.
