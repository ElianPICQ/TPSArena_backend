// Presence in Redis (all keys under realtime:):
//   realtime:presence:last_seen         zset  user_id -> last heartbeat (ms); membership = online
//   realtime:presence:{user_id}:conns   zset  connection id -> last heartbeat (ms): connections per user
//   realtime:presence:{user_id}         hash  activity (menu | in_match), TTL refreshed by heartbeats
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

// KEYS: hash, last_seen   ARGV: user_id, activity, ttl s   -> 1 if the user is online (activity stored)
const SET_ACTIVITY = new Script(`
if not redis.call('ZSCORE', KEYS[2], ARGV[1]) then return 0 end
redis.call('HSET', KEYS[1], 'activity', ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[3])
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

  async setActivity(userId: string, activity: Activity): Promise<boolean> {
    return (await SET_ACTIVITY.run(this.redis, [hashKey(userId), LAST_SEEN], [userId, activity, this.ttlSeconds])) === 1;
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
