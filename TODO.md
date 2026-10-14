# To do later

## Godot dedicated server
The dedicated server exists (game repo, `front/scripts/server/`) but only in LAN mode
(`LanLink`: no backend calls). Connecting it to the backend:
- **Server List: routes for the game server's events.** `POST /servers/{id}/match-finished` and
  `POST /servers/{id}/player-match`, with `Authorization: Bearer <server_token>`. Validate the body
  with `MatchFinished` / `PlayerMatchChanged`, check that `server_id` is the caller's, publish with
  `tpsarena_common.events.publish`. Give the `serverlist` ACL user write access to
  `match.finished` and `player.match` (`infra/redis/start.sh`), and add the tests (and the requests in the local Bruno collection).
- **`ServerListLink`** (game repo, next to `lan_link.gd`): register with `GAME_SERVER_SECRET`, a
  heartbeat every 5 s (a `Timer` and `HTTPRequest`), verify tickets, send the two events,
  deregister on exit. Start the match when every reserved slot is connected or 30 s after the
  reservation, instead of the LAN lobby leader.
- **`max_players`: already enforced** by the game server: it refuses a new player when 8 are
  already connected, even with a valid ticket. Keep it that way: the Server List's player count
  can be off by one for up to a heartbeat (5 s), because a heartbeat sent just before the player
  connected overwrites the count with the old number.
- **Keep the server token.** `POST /servers` (shared `GAME_SERVER_SECRET`) returns a
  `server_token`; heartbeats, deregistration and ticket checks must send it as
  `Authorization: Bearer <server_token>`. On `404 server_not_found` from a heartbeat, register
  again and use the new id and token.

## Before the public release
- Move email sending from the Gmail account to an own domain + transactional provider
  (see `project-context.md`).
- Index the username search. `GET /users/search` filters on `lower(username) LIKE '%…%'`, which
  the `ix_users_username_lower` B-tree can't serve, so each search scans the users table. With
  many accounts, add a `pg_trgm` GIN index on `lower(username)` (migration with
  `CREATE EXTENSION pg_trgm`).
