# Reading the databases

Each service has its own PostgreSQL container. To read its tables, open `psql` inside the
container from the repository root, with the full stack running:

```sh
docker compose exec auth-db     psql -U auth_svc     -d auth
docker compose exec social-db   psql -U social_svc   -d social
docker compose exec stats-db    psql -U stats_svc    -d stats
docker compose exec realtime-db psql -U realtime_svc -d realtime
```

| Database | Schema | Tables |
|---|---|---|
| `auth` | `auth` | `users`, `refresh_tokens`, `email_codes`, `outbox` |
| `social` | `social` | `players`, `friend_requests`, `friendships`, `outbox` |
| `stats` | `stats` | `matches`, `match_players`, `player_stats`, `player_achievements`, `outbox` |
| `realtime` | `chat` | `messages` |

Inside `psql`:

```sql
\dt auth.*        -- list a schema's tables
\d auth.users     -- describe a table
SELECT id, username, email, email_verified_at FROM auth.users ORDER BY created_at DESC LIMIT 10;
SELECT * FROM stats.player_stats ORDER BY xp DESC LIMIT 10;
SELECT * FROM chat.messages ORDER BY id DESC LIMIT 10;
\q                -- quit
```

To run a single query without opening the shell:

```sh
docker compose exec auth-db psql -U auth_svc -d auth -c "SELECT username, created_at FROM auth.users;"
```

## Resetting every database

To start over with empty databases, remove the data volumes and start the stack again from
the repository root. This deletes every user, profile, match and chat message, and the Redis
state (sessions, parties, leaderboards, stream progress). The services recreate their tables
at startup.

```sh
docker compose down -v
docker compose up -d
```
