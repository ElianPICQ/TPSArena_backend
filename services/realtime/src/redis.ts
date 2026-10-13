import { createHash } from "node:crypto";
import { Redis } from "ioredis";

export function connect(url: string, name: string): Redis {
  // ioredis reconnects on its own after a lost connection (e.g. Redis restarted).
  // No ready check: it runs INFO, which the service's Redis user isn't allowed (-@dangerous).
  return new Redis(url, { connectionName: `realtime-${name}`, maxRetriesPerRequest: 3, enableReadyCheck: false });
}

/** A Lua script run with EVALSHA (loaded on first use). Keys go through KEYS, as Redis ACLs require. */
export class Script {
  private readonly sha: string;

  constructor(private readonly source: string) {
    this.sha = createHash("sha1").update(source).digest("hex");
  }

  async run(redis: Redis, keys: string[], args: (string | number)[]): Promise<unknown> {
    try {
      return await redis.evalsha(this.sha, keys.length, ...keys, ...args);
    } catch (error) {
      if (!String(error).includes("NOSCRIPT")) throw error;
      return redis.eval(this.source, keys.length, ...keys, ...args);
    }
  }
}
