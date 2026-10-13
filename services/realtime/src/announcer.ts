import type { Redis } from "ioredis";
import type { Bus } from "./bus.js";
import { publish } from "./events.js";
import type { Presence } from "./presence.js";

/** Tells everyone concerned that a player's presence changed. */
export class Announcer {
  constructor(
    private readonly redis: Redis,
    private readonly presence: Presence,
    private readonly bus: Bus,
    private readonly maxlen: number,
  ) {}

  /** Came online: Social and friends learn it. */
  async online(userId: string): Promise<void> {
    await publish(this.redis, "presence.changed", { user_id: userId, status: "online" }, this.maxlen);
    await this.changed(userId);
  }

  /** Went offline (after the grace period): Social removes the player from their party. */
  async offline(userId: string): Promise<void> {
    await publish(this.redis, "presence.changed", { user_id: userId, status: "offline" }, this.maxlen);
    await this.bus.publish({ kind: "presence", presence: { user_id: userId, status: "offline", activity: null } });
  }

  /** Activity changed (menu / in match), or a fresh online status: pushed to online friends only. */
  async changed(userId: string): Promise<void> {
    const [presence] = await this.presence.get([userId]);
    await this.bus.publish({ kind: "presence", presence: presence! });
  }
}
