// Redis Streams consumer, behaving like tpsarena_common.streams.StreamConsumer (Python):
// - one consumer group per consuming service; instances of the service share it
// - XACK after handling; a failed event stays pending and is retried through XAUTOCLAIM
// - at-least-once delivery: event ids already handled are skipped ({prefix}:handled:{group}:{id})
// - undecodable events are logged and acknowledged so they don't block the group
// Unlike the Python base (see TODO.md), a lost Redis connection doesn't stop the loop.
import type { Redis } from "ioredis";
import { decode, UndecodableEvent, type AnyEvent } from "./events.js";
import { log } from "./log.js";

export type Handler = (event: AnyEvent) => Promise<void>;

interface Options {
  consumer: string;
  blockMs?: number;
  claimIdleMs?: number;
  handledTtlSeconds?: number;
}

type Entry = [id: string, fields: string[]];

function toObject(fields: string[]): Record<string, string> {
  const object: Record<string, string> = {};
  for (let i = 0; i + 1 < fields.length; i += 2) object[fields[i]!] = fields[i + 1]!;
  return object;
}

export class StreamConsumer {
  private running = false;
  private readonly blockMs: number;
  private readonly claimIdleMs: number;
  private readonly handledTtlSeconds: number;

  constructor(
    private readonly redis: Redis, // a dedicated connection: XREADGROUP blocks it
    private readonly keyPrefix: string,
    private readonly group: string,
    private readonly streams: string[],
    private readonly handler: Handler,
    private readonly options: Options,
  ) {
    this.blockMs = options.blockMs ?? 5000;
    this.claimIdleMs = options.claimIdleMs ?? 60_000;
    this.handledTtlSeconds = options.handledTtlSeconds ?? 7 * 24 * 3600;
  }

  async ensureGroups(): Promise<void> {
    for (const stream of this.streams) {
      try {
        await this.redis.xgroup("CREATE", stream, this.group, "0", "MKSTREAM");
      } catch (error) {
        if (!String(error).includes("BUSYGROUP")) throw error;
      }
    }
  }

  async handle(stream: string, id: string, fields: string[]): Promise<void> {
    let event: AnyEvent;
    try {
      event = decode(toObject(fields));
    } catch (error) {
      if (!(error instanceof UndecodableEvent)) throw error;
      log("error", "dropping undecodable message", { stream, id, reason: error.message });
      await this.redis.xack(stream, this.group, id);
      return;
    }
    const handledKey = `${this.keyPrefix}:handled:${this.group}:${event.eventId}`;
    if (!(await this.redis.exists(handledKey))) {
      try {
        await this.handler(event);
      } catch (error) {
        log("error", "handler failed, will retry", { type: event.type, id, error: String(error) });
        return;
      }
      await this.redis.set(handledKey, "1", "EX", this.handledTtlSeconds);
    }
    await this.redis.xack(stream, this.group, id);
  }

  async reclaim(): Promise<void> {
    for (const stream of this.streams) {
      let start = "0-0";
      do {
        const [next, entries] = (await this.redis.xautoclaim(
          stream, this.group, this.options.consumer, this.claimIdleMs, start, "COUNT", 100,
        )) as [string, Entry[]];
        for (const [id, fields] of entries) if (fields) await this.handle(stream, id, fields);
        start = next;
      } while (start !== "0-0");
    }
  }

  async pollOnce(): Promise<void> {
    const response = (await this.redis.xreadgroup(
      "GROUP", this.group, this.options.consumer, "COUNT", 10, "BLOCK", this.blockMs,
      "STREAMS", ...this.streams, ...this.streams.map(() => ">"),
    )) as [string, Entry[]][] | null;
    for (const [stream, entries] of response ?? []) {
      for (const [id, fields] of entries) await this.handle(stream, id, fields);
    }
  }

  async run(): Promise<void> {
    this.running = true;
    let lastReclaim = 0;
    let groupsReady = false;
    log("info", "consuming streams", { streams: this.streams, group: this.group, consumer: this.options.consumer });
    while (this.running) {
      try {
        if (!groupsReady) {
          await this.ensureGroups();
          groupsReady = true;
        }
        if (Date.now() - lastReclaim > this.claimIdleMs) {
          await this.reclaim();
          lastReclaim = Date.now();
        }
        await this.pollOnce();
      } catch (error) {
        if (!this.running) break;
        if (String(error).includes("NOGROUP")) groupsReady = false;
        log("warn", "stream consumer error, retrying", { error: String(error) });
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    }
  }

  stop(): void {
    this.running = false;
  }
}
