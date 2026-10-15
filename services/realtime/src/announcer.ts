import type { Redis } from "ioredis";
import type { Bus } from "./bus.js";
import { publish } from "./events.js";
import type { Presence, PresenceChange } from "./presence.js";

/** Tells everyone concerned that a player's presence changed. */
export class Announcer {
  constructor(
    private readonly redis: Redis,
    private readonly presence: Presence,
    private readonly bus: Bus,
    private readonly maxlen: number,
  ) {}

  /** After a connect or heartbeat. Social learns when the game opens; friends get the new presence. */
  async touched(userId: string, change: PresenceChange): Promise<void> {
    if (change.game) await publish(this.redis, "presence.changed", { user_id: userId, status: "online" }, this.maxlen);
    if (change.online || change.game) await this.changed(userId);
  }

  /**
   * After an offline check (grace period over, or a crashed instance swept). Social removes the
   * player from their party when the game closed: the companion app alone can't play.
   */
  async checked(userId: string, change: PresenceChange): Promise<void> {
    if (change.game || change.online) {
      await publish(this.redis, "presence.changed", { user_id: userId, status: "offline" }, this.maxlen);
    }
    if (change.online) {
      await this.bus.publish({ kind: "presence", presence: { user_id: userId, status: "offline", activity: null } });
    } else if (change.game) {
      await this.changed(userId); // still online, on the companion app
    }
  }

  /** Activity changed (menu / in match / mobile), or a fresh online status: pushed to online friends only. */
  async changed(userId: string): Promise<void> {
    const [presence] = await this.presence.get([userId]);
    await this.bus.publish({ kind: "presence", presence: presence! });
  }
}
