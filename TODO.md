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

## Party events can still be lost
Social's party events (`party.updated`, `party.match_ready`, ...) are published right after the
Lua script that changes the party. The party lives in Redis too, so it isn't the outbox case, but
a lost connection between the script and the `XADD` still loses the event: Real-time's party chat
membership and the members' screens stay out of date until the next party change.
Fix if it shows up: add the event from inside the Lua scripts, like the Server List does
(the script builds the `data` JSON; a test decodes it with the shared models).

## Server List down for more than 30 s
The sweeper reads a crashed server's `party_id` from its registry hash, which expires 30 s after
the last heartbeat. If no Server List instance runs a sweep in that time, `server.removed` comes
without `party_id` and the party stays `in_match` (members can leave and make a new party).
Fix if it matters: keep `server_id -> party_id` in a separate key without the short TTL, or have
the Social worker check its `in_match` parties against the Server List periodically.

## Before the public release
- Move email sending from the Gmail account to an own domain + transactional provider
  (see `project-context.md`).
