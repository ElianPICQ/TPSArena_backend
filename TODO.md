# To do later

## Outbox for Auth and Stats events
Auth and Stats commit to PostgreSQL and then publish to Redis Streams as two separate steps.
If Redis fails between the commit and the publish, the event is lost:
- Auth (`POST /me/delete` → `user.deleted`, `POST /register` and the code routes → `auth.emails`):
  a deleted user's data stays in Social, Stats and Real-time, or a code email is never sent.
- Stats worker (`match.finished` → `stats.progress`): the match, XP and achievements are saved,
  but the level-up / achievement notifications are never sent. The retry finds the match
  already stored and skips it, so they're lost rather than doubled.

Fix: an `outbox` table in each service's schema. Write the event in the same transaction as the
change, and have the service's worker publish pending rows to their stream and mark them sent.
Consumers already skip duplicate `event_id`s, so publishing a row twice after a crash is safe.
The outbox publisher belongs in `libs/common`, next to the consumer base.

## Per-server credentials for game servers
All game servers share one `GAME_SERVER_SECRET`, so any of them can send heartbeats, deregister
or verify tickets as another server (it only needs the other server's id).

Fix: `POST /servers` returns a random per-server token, stored hashed in the `server:{id}` hash.
The other game server routes then require that token, checked against the server id in the URL
or body. The shared secret stays, but only for registering.

## Godot dedicated server must enforce `max_players`
The Server List API's player count can be off by one for up to a heartbeat (5 s): a verified
ticket counts the player right away, but a heartbeat sent just before the player connected
overwrites it with the old number. A stranger could then get a ticket for a full match.

Fix (in the game server, GDScript): refuse a new peer when `multiplayer.get_peers().size()`
already equals `max_players`, even with a valid ticket.

## Party stuck in a match after a game server crash
A party goes back to `lobby` only when Social receives `match.finished` from the party's server.
If that server crashes first, the event never comes: the party stays `in_match` and can't
launch again (members can only leave and make a new party).

Fix: when the Server List drops a server that missed its heartbeats, publish an event
(e.g. `server.lost` with the server id and its `party_id`), and have the Social worker treat it
like `match.finished`. Alternative: a periodic check in the Social worker for `in_match`
parties whose server no longer exists in the Server List.

## Find players by username
Friend requests take a user id, but players know each other's usernames. Auth needs a lookup
such as `GET /users/by-username/{username}` (exact, case-insensitive match, only public fields,
rate limited so it can't be used to list every account), exposed through the BFF.

## Stream workers survive a Redis restart
The API services reconnect to Redis on their own, but the stream workers (`StreamConsumer` in
`libs/common`) exit on a lost connection and rely on Docker's `restart: unless-stopped`.
Fix: in `StreamConsumer.run`, catch connection errors, wait with backoff and resume (and use
the same `Retry` settings in `consumer_redis`); release a new `tpsarena-common` version and
upgrade the services.

## Close WebSockets on logout and password reset
Logout and password reset revoke refresh tokens, but an open WebSocket stays connected until its
access token expires (up to 15 minutes), because Real-time only checks tokens at `auth` and on
expiry.
Fix: Auth publishes an event when sessions are revoked (e.g. `auth.sessions_revoked` with the user
id, on logout-all and password reset), and Real-time closes that player's sockets (4001) like it
does for `user.deleted`. Plain logout of one device can keep the current behaviour.

## Before the public release
- Move email sending from the Gmail account to an own domain + transactional provider
  (see `project-context.md`).
