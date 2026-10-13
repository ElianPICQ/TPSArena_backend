import type { Redis } from "ioredis";
import type pg from "pg";
import { Script } from "./redis.js";

export interface FriendMessage {
  id: string;
  from_user_id: string;
  to_user_id: string;
  body: string;
  created_at: string;
}

const COLUMNS = "id::text, from_user_id::text, to_user_id::text, body, created_at";

function toMessage(row: Record<string, unknown>): FriendMessage {
  return { ...(row as unknown as FriendMessage), created_at: (row.created_at as Date).toISOString() };
}

/** Friend-to-friend messages (PostgreSQL, database `realtime`, schema `chat`). */
export class FriendMessages {
  constructor(private readonly db: pg.Pool) {}

  async send(from: string, to: string, body: string): Promise<FriendMessage> {
    const { rows } = await this.db.query(
      `INSERT INTO chat.messages (from_user_id, to_user_id, body) VALUES ($1, $2, $3) RETURNING ${COLUMNS}`,
      [from, to, body],
    );
    return toMessage(rows[0]);
  }

  async markDelivered(ids: string[]): Promise<void> {
    if (ids.length === 0) return;
    await this.db.query("UPDATE chat.messages SET delivered_at = now() WHERE id = ANY($1::bigint[]) AND delivered_at IS NULL", [ids]);
  }

  async undelivered(to: string): Promise<FriendMessage[]> {
    const { rows } = await this.db.query(
      `SELECT ${COLUMNS} FROM chat.messages WHERE to_user_id = $1 AND delivered_at IS NULL ORDER BY id`,
      [to],
    );
    return rows.map(toMessage);
  }

  /** Messages between the two players, newest first, before message id `before` if given. */
  async conversation(me: string, other: string, limit: number, before?: string): Promise<FriendMessage[]> {
    const { rows } = await this.db.query(
      `SELECT ${COLUMNS} FROM chat.messages
       WHERE LEAST(from_user_id, to_user_id) = LEAST($1::uuid, $2::uuid)
         AND GREATEST(from_user_id, to_user_id) = GREATEST($1::uuid, $2::uuid)
         AND ($3::bigint IS NULL OR id < $3::bigint)
       ORDER BY id DESC LIMIT $4`,
      [me, other, before ?? null, limit],
    );
    return rows.map(toMessage);
  }

  async deleteUser(userId: string): Promise<void> {
    await this.db.query("DELETE FROM chat.messages WHERE from_user_id = $1 OR to_user_id = $1", [userId]);
  }

  async deleteOlderThan(days: number): Promise<number> {
    const { rowCount } = await this.db.query(
      "DELETE FROM chat.messages WHERE created_at < now() - make_interval(days => $1)",
      [days],
    );
    return rowCount ?? 0;
  }
}

export interface PartyMessage {
  party_id: string;
  from_user_id: string;
  body: string;
  sent_at: string;
}

const SAFETY_TTL = 7 * 24 * 3600; // in case a party.disbanded is ever missed

// KEYS: members, version, messages   ARGV: version, members...   -> previous members, or nil if not newer
const APPLY_SNAPSHOT = new Script(`
local current = tonumber(redis.call('GET', KEYS[2]) or '0')
if tonumber(ARGV[1]) <= current then return false end
local previous = redis.call('SMEMBERS', KEYS[1])
redis.call('DEL', KEYS[1])
if #ARGV > 1 then redis.call('SADD', KEYS[1], unpack(ARGV, 2)) end
redis.call('SET', KEYS[2], ARGV[1])
for _, key in ipairs(KEYS) do redis.call('EXPIRE', key, ${SAFETY_TTL}) end
return previous
`);

// KEYS: members, version, messages   -> members before deletion
const DISBAND = new Script(`
local previous = redis.call('SMEMBERS', KEYS[1])
redis.call('DEL', KEYS[1], KEYS[2], KEYS[3])
return previous
`);

/**
 * Party chat (Redis):
 *   realtime:party_chat:{party_id}           list  recent messages (JSON), newest first, capped
 *   realtime:party_chat:{party_id}:members   set   copy of the members, from party.updated
 *   realtime:party_chat:{party_id}:version   the party.updated version that set the members
 */
export class PartyChat {
  constructor(
    private readonly redis: Redis,
    private readonly maxMessages: number,
  ) {}

  private keys(partyId: string): string[] {
    const base = `realtime:party_chat:${partyId}`;
    return [`${base}:members`, `${base}:version`, base];
  }

  /** Apply a party.updated snapshot if it's newer. Returns the previous members, or null if it was stale. */
  async applySnapshot(partyId: string, version: number, members: string[]): Promise<string[] | null> {
    return (await APPLY_SNAPSHOT.run(this.redis, this.keys(partyId), [version, ...members])) as string[] | null;
  }

  async disband(partyId: string): Promise<string[]> {
    return (await DISBAND.run(this.redis, this.keys(partyId), [])) as string[];
  }

  async members(partyId: string): Promise<string[]> {
    return this.redis.smembers(this.keys(partyId)[0]!);
  }

  async isMember(partyId: string, userId: string): Promise<boolean> {
    return (await this.redis.sismember(this.keys(partyId)[0]!, userId)) === 1;
  }

  async post(message: PartyMessage): Promise<void> {
    const list = this.keys(message.party_id)[2]!;
    await this.redis
      .multi()
      .lpush(list, JSON.stringify(message))
      .ltrim(list, 0, this.maxMessages - 1)
      .expire(list, SAFETY_TTL)
      .exec();
  }

  /** Oldest first. */
  async history(partyId: string): Promise<PartyMessage[]> {
    const raw = await this.redis.lrange(this.keys(partyId)[2]!, 0, this.maxMessages - 1);
    return raw.map((m) => JSON.parse(m) as PartyMessage).reverse();
  }
}
