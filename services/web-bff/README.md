# Web BFF

Entry point of the companion PWA (served by `TPSArena_Site` under `/app/`): login, player stats,
progression, match history, leaderboards, friends and friend message history. It calls **Auth**,
**Stats**, **Social** (friends only) and **Real-time** (presence, history), and adds player names
from Auth to leaderboards, match results and the friend list. The chat itself goes over the
Real-time WebSocket, which the site's nginx relays as `/ws` (the PWA says `"client": "mobile"`).
No parties, servers or account management.

Port 8006, published on the host's loopback only. Players reach it through the site's nginx,
which serves it under `/api/` on the same origin as the PWA (no CORS) over the shared Docker
network `tpsarena-web`. Owns: nothing (stateless).

## Differences with the game client's BFF
- **Refresh token in a cookie**: `/auth/login` and `/auth/refresh` return the access token and the
  user (`user_id`, `username`) in the body, and put the refresh token in the `tpsarena_refresh`
  cookie (`HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/api/auth`, 30 days). Page scripts never
  see it. `/auth/logout` revokes it with Auth and removes the cookie.
- **A subset**: no registration, password reset, profiles, loadouts, parties or servers.
  Accounts are created in the game.
- **Player IP**: uvicorn trusts `X-Forwarded-For` (`--proxy-headers`), which the site's nginx
  overwrites with the player's address. That is safe only because the port isn't on the LAN.

## Endpoints
| Method | Path | Upstream |
|---|---|---|
| POST | `/auth/login` (`identifier`, `password`) | Auth `/login` + `/me` |
| POST | `/auth/refresh` (cookie) | Auth `/refresh` + `/me` |
| POST | `/auth/logout` (cookie) | Auth `/logout` |
| GET | `/players/{id}/stats`, `/progression/{id}`, `/players/{id}/matches` | Stats |
| GET | `/matches/{id}`, `/leaderboards/{type}`, `/leaderboards/{type}/around/{id}` | Stats + names from Auth |
| GET | `/users/search?username=` (outside `/auth/`: the refresh cookie isn't sent) | Auth |
| GET | `/friends` | Social + names from Auth |
| POST | `/friends/requests` (`user_id`), `/friends/requests/{id}/accept`, `/friends/requests/{id}/decline` | Social |
| DELETE | `/friends/{id}` | Social |
| GET | `/presence?ids=`, `/conversations/{friendId}/messages` | Real-time |

Errors are the services' own bodies (`{"error", "message"}`); a service down → `503
<service>_unavailable` (`auth`, `stats`, `social`, `realtime`), non-JSON reply → `502 bad_gateway`.

## Run it alone
```
cp .env.example .env   # optional: the defaults work for local development
docker compose up --build
```

## Tests
`uv run pytest` — the other services are mocked.

## In the full stack
The root `docker-compose.yml` includes this folder's `compose.yaml` and supplies the services'
addresses from the root `.env`. Real-time joins the `tpsarena-web` network too
(`infra/fullstack/realtime.yaml`), for the site's `/ws`.
