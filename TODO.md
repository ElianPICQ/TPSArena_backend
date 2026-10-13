# To do later

## Godot dedicated server
To do when the game server is written (GDScript):
- **Enforce `max_players`.** The Server List API's player count can be off by one for up to a
  heartbeat (5 s): a verified ticket counts the player right away, but a heartbeat sent just
  before the player connected overwrites it with the old number. A stranger could then get a
  ticket for a full match. Refuse a new peer when `multiplayer.get_peers().size()` already
  equals `max_players`, even with a valid ticket.
- **Keep the server token.** `POST /servers` (shared `GAME_SERVER_SECRET`) returns a
  `server_token`; heartbeats, deregistration and ticket checks must send it as
  `Authorization: Bearer <server_token>`. On `404 server_not_found` from a heartbeat, register
  again and use the new id and token.

## Before the public release
- Move email sending from the Gmail account to an own domain + transactional provider
  (see `project-context.md`).
