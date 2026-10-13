// Delivery between instances through Redis Pub/Sub (channel realtime:deliver). A stream event is
// handled by one instance, but the players it concerns may be connected to any instance: every
// instance receives each bus message and acts on its own sockets.
import type { Redis } from "ioredis";
import type { PresenceInfo } from "./presence.js";
import type { ServerMessage } from "./protocol.js";

export type BusMessage =
  | { kind: "push"; userIds: string[]; payload: ServerMessage }
  | { kind: "presence"; presence: PresenceInfo }
  | { kind: "friendship"; a: string; b: string; op: "add" | "remove" }
  | { kind: "close"; userId: string; code: number; reason: string };

const CHANNEL = "realtime:deliver";

export class Bus {
  constructor(
    private readonly publisher: Redis,
    private readonly subscriber: Redis, // a dedicated connection: subscribed connections can't run other commands
  ) {}

  async start(onMessage: (message: BusMessage) => void | Promise<void>): Promise<void> {
    this.subscriber.on("message", (_channel: string, raw: string) => {
      void Promise.resolve(onMessage(JSON.parse(raw) as BusMessage)).catch((error) =>
        console.error("bus handler failed", error),
      );
    });
    await this.subscriber.subscribe(CHANNEL);
  }

  async publish(message: BusMessage): Promise<void> {
    await this.publisher.publish(CHANNEL, JSON.stringify(message));
  }
}
