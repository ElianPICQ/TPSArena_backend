// Presence in Redis (all keys under realtime:):
//   realtime:presence:last_seen         zset  user_id -> last heartbeat (ms); membership = online
//   realtime:presence:{user_id}:conns   zset  connection id -> last heartbeat (ms): connections per user
//   realtime:presence:{user_id}         hash  activity (menu | in_match), server_id (in a match),
//                                             TTL refreshed by heartbeats
//   realtime:server:{server_id}:players set   user ids in that game server's match (from player.match),
//                                             to reset them if the server is lost (server.removed)
// Lua scripts make "came online" and "went offline" happen exactly once, even with several
// instances: only the call that adds/removes the user from last_seen reports the change.
import type { Redis } from "ioredis";
import { Script } from "./redis.js";

export type Activity = "menu" | "in_match";

export interface PresenceInfo {
  user_id: string;
  status: "online" | "offline";
  activity: Activity | null;
}

const LAST_SEEN = "realtime:presence:last_seen";
const hashKey = (userId: string) => `realtime:presence:${userId}`;
const connsKey = (userId: string) => `realtime:presence:${userId}:conns`;
const serverPlayersKey = (serverId: string) => `realtime:server:${serverId}:players`;
const SERVER_PLAYERS_TTL_SECONDS = 24 * 3600; // refreshed by each join; outlives any match

// KEYS: hash, conns, last_seen   ARGV: user_id, connection id, now ms, ttl s   -> 1 if the user came online
const TOUCH = new Script(`
local came_online = redis.call('ZADD', KEYS[3], ARGV[3], ARGV[1])
redis.call('ZADD', KEYS[2], ARGV[3], ARGV[2])
redis.call('PEXPIRE', KEYS[2], tonumber(ARGV[4]) * 1000)
redis.call('HSETNX', KEYS[1], 'activity', 'menu')
redis.call('EXPIRE', KEYS[1], ARGV[4])
return came_online
`);

// KEYS: hash, conns, last_seen   ARGV: user_id, stale before (ms)   -> 1 if the user went offline
const CHECK_OFFLINE = new Script(`
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', ARGV[2])
if redis.call('ZCARD', KEYS[2]) > 0 then return 0 end
if redis.call('ZREM', KEYS[3], ARGV[1]) == 1 then
  redis.call('DEL', KEYS[1])
  return 1
end
return 0
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

  /** Connect or heartbeat. True if the user just came online. */
  async touch(userId: string, connectionId: string): Promise<boolean> {
    const keys = [hashKey(userId), connsKey(userId), LAST_SEEN];
    return (await TOUCH.run(this.redis, keys, [userId, connectionId, Date.now(), this.ttlSeconds])) === 1;
  }

  async dropConnection(userId: string, connectionId: string): Promise<void> {
    await this.redis.zrem(connsKey(userId), connectionId);
  }

  /** True if the user has no live connection left and was marked offline by this call. */
  async checkOffline(userId: string): Promise<boolean> {
    const keys = [hashKey(userId), connsKey(userId), LAST_SEEN];
    const staleBefore = Date.now() - this.ttlSeconds * 1000;
    return (await CHECK_OFFLINE.run(this.redis, keys, [userId, staleBefore])) === 1;
  }

  /** Users whose last heartbeat is older than the TTL (e.g. their instance crashed). */
  async staleUsers(): Promise<string[]> {
    return this.redis.zrangebyscore(LAST_SEEN, "-inf", Date.now() - this.ttlSeconds * 1000);
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
    }
    const results = (await pipeline.exec()) ?? [];
    return userIds.map((user_id, i) => {
      const online = results[i * 2]?.[1] != null;
      const activity = (results[i * 2 + 1]?.[1] as Activity | null) ?? "menu";
      return { user_id, status: online ? "online" : "offline", activity: online ? activity : null };
    });
  }

  /** Account deleted: forget everything. True if the user was online. */
  async remove(userId: string): Promise<boolean> {
    const [[, removed]] = (await this.redis
      .multi()
      .zrem(LAST_SEEN, userId)
      .del(hashKey(userId), connsKey(userId))
      .exec()) as [[null, number], [null, number]];
    return removed === 1;
  }
}
