// Presence in Redis (all keys under realtime:):
//   realtime:presence:last_seen         zset  user_id -> last heartbeat (ms); membership = online
//   realtime:presence:{user_id}:conns   zset  connection id -> last heartbeat (ms): connections per user
//   realtime:presence:in_game           zset  user_id -> last heartbeat of a game client (ms); membership =
//                                             the game is open (a socket that isn't the mobile app)
//   realtime:presence:{user_id}:game    zset  the game client connections among :conns
//   realtime:presence:{user_id}         hash  activity (menu | in_match), server_id (in a match),
//                                             TTL refreshed by heartbeats
//   realtime:server:{server_id}:players set   user ids in that game server's match (from player.match),
//                                             to reset them if the server is lost (server.removed)
// Lua scripts make "came online" and "went offline" happen exactly once, even with several
// instances: only the call that adds/removes the user from last_seen reports the change.
// Friends see a player online as long as any socket is open; with only the companion app
// (mobile sockets), the shown activity is "mobile". Social only cares about the game: it gets
// presence.changed when the game opens or closes (see Announcer).
import type { Redis } from "ioredis";
import { Script } from "./redis.js";

export type Activity = "menu" | "in_match" | "mobile";
export type ClientKind = "game" | "mobile";

/** What a touch or an offline check changed (both can happen at once). */
export interface PresenceChange {
  online: boolean; // came online / went offline (any socket)
  game: boolean; // the game opened / closed (game sockets only)
}

export interface PresenceInfo {
  user_id: string;
  status: "online" | "offline";
  activity: Activity | null;
}

const LAST_SEEN = "realtime:presence:last_seen";
const hashKey = (userId: string) => `realtime:presence:${userId}`;
const connsKey = (userId: string) => `realtime:presence:${userId}:conns`;
const IN_GAME = "realtime:presence:in_game";
const gameConnsKey = (userId: string) => `realtime:presence:${userId}:game`;
const serverPlayersKey = (serverId: string) => `realtime:server:${serverId}:players`;
const SERVER_PLAYERS_TTL_SECONDS = 24 * 3600; // refreshed by each join; outlives any match

// KEYS: hash, conns, last_seen, game conns, in_game   ARGV: user_id, connection id, now ms, ttl s, game (1|0)
// -> bit 1: the user came online, bit 2: the game opened
const TOUCH = new Script(`
local changed = redis.call('ZADD', KEYS[3], ARGV[3], ARGV[1])
redis.call('ZADD', KEYS[2], ARGV[3], ARGV[2])
redis.call('PEXPIRE', KEYS[2], tonumber(ARGV[4]) * 1000)
if ARGV[5] == '1' then
  changed = changed + 2 * redis.call('ZADD', KEYS[5], ARGV[3], ARGV[1])
  redis.call('ZADD', KEYS[4], ARGV[3], ARGV[2])
  redis.call('PEXPIRE', KEYS[4], tonumber(ARGV[4]) * 1000)
end
redis.call('HSETNX', KEYS[1], 'activity', 'menu')
redis.call('EXPIRE', KEYS[1], ARGV[4])
return changed
`);

// KEYS: hash, conns, last_seen, game conns, in_game   ARGV: user_id, stale before (ms)
// -> bit 1: the user went offline, bit 2: the game closed
const CHECK_OFFLINE = new Script(`
local changed = 0
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[4], '-inf', ARGV[2])
if redis.call('ZCARD', KEYS[4]) == 0 and redis.call('ZREM', KEYS[5], ARGV[1]) == 1 then
  changed = 2
  -- Only the companion app left: a match can't go on.
  redis.call('HSET', KEYS[1], 'activity', 'menu')
  redis.call('HDEL', KEYS[1], 'server_id')
end
if redis.call('ZCARD', KEYS[2]) == 0 and redis.call('ZREM', KEYS[3], ARGV[1]) == 1 then
  redis.call('DEL', KEYS[1])
  changed = changed + 1
end
return changed
`);

// KEYS: hash, last_seen, server players   ARGV: user_id, joined | left, server_id, ttl s, server players ttl s
// -> 1 if the user is online and their activity was set. Leaving a server they aren't shown on
// (e.g. "left A" arriving after "joined B") changes nothing.
const MATCH_CHANGED = new Script(`
if ARGV[2] == 'joined' then
  redis.call('SADD', KEYS[3], ARGV[1])
  redis.call('EXPIRE', KEYS[3], ARGV[5])
else
  redis.call('SREM', KEYS[3], ARGV[1])
end
if not redis.call('ZSCORE', KEYS[2], ARGV[1]) then return 0 end
if ARGV[2] == 'joined' then
  redis.call('HSET', KEYS[1], 'activity', 'in_match', 'server_id', ARGV[3])
else
  local current = redis.call('HGET', KEYS[1], 'server_id')
  if current and current ~= ARGV[3] then return 0 end
  redis.call('HSET', KEYS[1], 'activity', 'menu')
  redis.call('HDEL', KEYS[1], 'server_id')
end
redis.call('EXPIRE', KEYS[1], ARGV[4])
return 1
`);

export class Presence {
  constructor(
    private readonly redis: Redis,
    private readonly ttlSeconds: number,
  ) {}

  private keys(userId: string): string[] {
    return [hashKey(userId), connsKey(userId), LAST_SEEN, gameConnsKey(userId), IN_GAME];
  }

  /** Connect or heartbeat. Says if the user just came online and if their game just opened. */
  async touch(userId: string, connectionId: string, kind: ClientKind = "game"): Promise<PresenceChange> {
    const args = [userId, connectionId, Date.now(), this.ttlSeconds, kind === "game" ? 1 : 0];
    return asChange((await TOUCH.run(this.redis, this.keys(userId), args)) as number);
  }

  async dropConnection(userId: string, connectionId: string): Promise<void> {
    await this.redis.multi().zrem(connsKey(userId), connectionId).zrem(gameConnsKey(userId), connectionId).exec();
  }

  /** Drops dead connections. Says if the user went offline and if their game closed, by this call. */
  async checkOffline(userId: string): Promise<PresenceChange> {
    const staleBefore = Date.now() - this.ttlSeconds * 1000;
    return asChange((await CHECK_OFFLINE.run(this.redis, this.keys(userId), [userId, staleBefore])) as number);
  }

  /** Users whose last heartbeat (any socket, or game sockets) is older than the TTL (e.g. their instance crashed). */
  async staleUsers(): Promise<string[]> {
    const before = Date.now() - this.ttlSeconds * 1000;
    const [online, inGame] = await Promise.all([
      this.redis.zrangebyscore(LAST_SEEN, "-inf", before),
      this.redis.zrangebyscore(IN_GAME, "-inf", before),
    ]);
    return [...new Set([...online, ...inGame])];
  }

  /** A game server reported the player joining or leaving its match. True if the shown activity was set. */
  async matchChanged(userId: string, serverId: string, action: "joined" | "left"): Promise<boolean> {
    const keys = [hashKey(userId), LAST_SEEN, serverPlayersKey(serverId)];
    const args = [userId, action, serverId, this.ttlSeconds, SERVER_PLAYERS_TTL_SECONDS];
    return (await MATCH_CHANGED.run(this.redis, keys, args)) === 1;
  }

  /** The server is gone without saying its players left. Returns the online players set back to "menu". */
  async serverRemoved(serverId: string): Promise<string[]> {
    const players = await this.redis.smembers(serverPlayersKey(serverId));
    const changed: string[] = [];
    for (const userId of players) {
      if (await this.matchChanged(userId, serverId, "left")) changed.push(userId);
    }
    await this.redis.del(serverPlayersKey(serverId));
    return changed;
  }

  async get(userIds: string[]): Promise<PresenceInfo[]> {
    if (userIds.length === 0) return [];
    const pipeline = this.redis.pipeline();
    for (const id of userIds) {
      pipeline.zscore(LAST_SEEN, id);
      pipeline.hget(hashKey(id), "activity");
      pipeline.zscore(IN_GAME, id);
    }
    const results = (await pipeline.exec()) ?? [];
    return userIds.map((user_id, i) => {
      const online = results[i * 3]?.[1] != null;
      const inGame = results[i * 3 + 2]?.[1] != null;
      const stored = (results[i * 3 + 1]?.[1] as Activity | null) ?? "menu";
      // A match only counts with the game open; without it, only the companion app is.
      const activity: Activity = !inGame ? "mobile" : stored;
      return { user_id, status: online ? "online" : "offline", activity: online ? activity : null };
    });
  }

  /** Account deleted: forget everything. True if the user was online. */
  async remove(userId: string): Promise<boolean> {
    const [[, removed]] = (await this.redis
      .multi()
      .zrem(LAST_SEEN, userId)
      .zrem(IN_GAME, userId)
      .del(hashKey(userId), connsKey(userId), gameConnsKey(userId))
      .exec()) as [[null, number], [null, number]];
    return removed === 1;
  }
}

function asChange(bits: number): PresenceChange {
  return { online: (bits & 1) === 1, game: (bits & 2) === 2 };
}
