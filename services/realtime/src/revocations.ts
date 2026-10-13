// Sessions revoked by Auth (auth.sessions_revoked: password reset, stolen refresh token). Access
// tokens issued before then stay valid until they expire, so Real-time remembers the time for as
// long as such a token can live and refuses them:
//   realtime:revoked:{user_id}   string  revoked_at (ms), TTL = access token lifetime
import type { Redis } from "ioredis";
import { Script } from "./redis.js";

const key = (userId: string) => `realtime:revoked:${userId}`;

// KEYS: key   ARGV: revoked_at ms, ttl s   (keeps the latest time if events arrive out of order)
const RECORD = new Script(`
local current = redis.call('GET', KEYS[1])
if current and tonumber(current) >= tonumber(ARGV[1]) then return 0 end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
return 1
`);

/** iat has whole seconds: a token from the same second as the revocation counts as newer. */
export function issuedBefore(issuedAt: number, revokedAt: number): boolean {
  return issuedAt < Math.floor(revokedAt / 1000) * 1000;
}

export class Revocations {
  constructor(
    private readonly redis: Redis,
    private readonly ttlSeconds: number,
  ) {}

  async record(userId: string, revokedAt: number): Promise<void> {
    await RECORD.run(this.redis, [key(userId)], [revokedAt, this.ttlSeconds]);
  }

  async isRevoked(userId: string, issuedAt: number): Promise<boolean> {
    const revokedAt = await this.redis.get(key(userId));
    return revokedAt !== null && issuedBefore(issuedAt, Number(revokedAt));
  }
}
