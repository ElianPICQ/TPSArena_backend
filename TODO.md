# To do later

## Godot dedicated server
The dedicated server is connected (game repo, `front/scripts/server/server_list_link.gd`:
registration, heartbeat, reservation, tickets, `player.match` / `match.finished` through the
Server List). Points to keep in mind:
- **Deregistration on Ctrl+C.** A killed server doesn't send `DELETE /servers/{id}`; the sweeper
  removes it after 15 s. A reservation made in those 15 s fails for the party (`server.removed`
  puts it back in the lobby).
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
