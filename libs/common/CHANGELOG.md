# Changelog — tpsarena-common

Services pin a released version (git tag `common-vX.Y.Z`). Add changes under "Unreleased";
`scripts/release_common.py X.Y.Z` turns that section into the release.

## Unreleased

## 0.2.0 (2026-10-13)

- `PlayerMatchChanged` event (`player.match` stream): a player joined or left a match.
- `export_schemas`: every event format as JSON Schema in `schemas/events.json` (released with
  the package), the contract for services not written in Python (Real-time, TypeScript).

## 0.1.0

- JWT verification with Auth's JWKS (`jwt_auth.JWKSVerifier`), cached, refetched on an unknown `kid`.
- Event models and the stream entry format (`events`): `event_id`, `type`, `data`.
- Redis Streams consumer base (`streams.StreamConsumer`): consumer groups, `XACK`, `XAUTOCLAIM`,
  duplicate skipping under the service's key prefix (`{prefix}:handled:...`), undecodable
  messages logged and acknowledged.
