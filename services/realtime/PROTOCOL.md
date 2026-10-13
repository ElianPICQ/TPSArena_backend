# Real-time WebSocket protocol

For the Godot client (`WebSocketPeer`). Connect to `ws://<host>:8005/ws`. Every message is a JSON
object with a `type`.

## Connecting
1. Open the socket, then send within **5 seconds**:
   `{"type": "auth", "token": "<access token>"}`
2. The server answers `hello` (below), then any friend messages received while you were offline.
3. Send `{"type": "heartbeat"}` every `heartbeat_interval` seconds (20). Without heartbeats for
   60 s the server considers the connection gone.
4. After each `POST /auth/refresh`, send `auth` again with the new access token. A connection whose
   token expires without a new one is closed (4001).

Closing the game: just close the socket. You are shown offline (and removed from your party) 30 s
later, unless you reconnect in between.

## Client → server
| Message | Effect |
|---|---|
| `{"type": "auth", "token": "..."}` | First message; again after each token refresh. |
| `{"type": "heartbeat"}` | Every 20 s. |
| `{"type": "friend_message", "to": "<user id>", "body": "...", "client_id": "..."}` | Message a friend (1–500 characters). `client_id` is optional, echoed in the ack. |
| `{"type": "party_message", "party_id": "...", "body": "..."}` | Message your party (1–500 characters). |
| `{"type": "party_history", "party_id": "..."}` | The last 100 messages of your party. |

Rate limit: 10 messages (friend + party) per 10 seconds per connection.

## Server → client
| Message | When |
|---|---|
| `{"type": "hello", "user_id", "heartbeat_interval": 20, "friends": [presence...]}` | After a successful first `auth`. |
| `{"type": "presence", "user_id", "status": "online"\|"offline", "activity": "menu"\|"in_match"\|null}` | A friend came online, went offline, started or left a match; also for a new friend. |
| `{"type": "friend_message", "message": {"id", "from_user_id", "to_user_id", "body", "created_at"}}` | A friend messaged you (live, or on connect if it was sent while you were offline). |
| `{"type": "friend_message_sent", "client_id", "message": {...}}` | Your message was saved. |
| `{"type": "party_message", "message": {"party_id", "from_user_id", "body", "sent_at"}}` | A party member (including you, from any device) posted. |
| `{"type": "party_history", "party_id", "messages": [...]}` | Answer to `party_history`, oldest first. |
| `{"type": "notification", "event": "...", "data": {...}}` | See below. |
| `{"type": "error", "error": "<code>", "message": "..."}` | A request was refused (`not_authenticated`, `invalid_token`, `token_expired`, `session_revoked`, `not_friends`, `not_in_party`, `invalid_body`, `rate_limited`, `service_unavailable`, ...). |

### Notifications (`event` → `data`)
`data` has the same fields as the event in `contracts/events.json`, except where noted.
| `event` | Sent to |
|---|---|
| `friend.request_sent` | the player who received the request |
| `friend.request_removed` | both players (declined or cancelled) |
| `friend.added`, `friend.removed` | both players |
| `party.invite_sent` | the invited player |
| `party.updated` | every member, and players who were just removed (left or kicked); full snapshot, keep the highest `version` |
| `party.match_ready` | each member, with **only their own** `ticket` (instead of the `tickets` map): connect to `address:port` with it |
| `party.disbanded` | the last members |
| `progress.level_up`, `progress.achievement_unlocked` | the player |

## Close codes
| Code | Meaning | Client should |
|---|---|---|
| 4001 | Token missing, invalid or expired | Refresh the token (or log in again), then reconnect. |
| 4001, reason `session_revoked` | Every session was ended (password reset, or a stolen refresh token) | Go back to the login screen (refreshing will fail). |
| 4003 | Account deleted | Go back to the login screen. |
| 4008 | No `auth` within 5 s | Reconnect and send `auth` first. |
| 1013 | A service is unavailable (couldn't load your friends) | Reconnect after a few seconds. |
| 1001 | Server restarting | Reconnect. |

## HTTP (through the BFF)
- `GET /presence?ids=a,b` — presence of those players, **only for your friends** (others are left out).
- `GET /conversations/{friendId}/messages?limit=20&before=<next_cursor>` — friend message history,
  newest first.
