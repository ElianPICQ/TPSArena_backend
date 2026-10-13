import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import type { JWTVerifyGetKey } from "jose";
import pg from "pg";
import { WebSocketServer } from "ws";
import { Announcer } from "./announcer.js";
import { Bus } from "./bus.js";
import { FriendMessages, PartyChat } from "./chat.js";
import type { Config } from "./config.js";
import { createEventHandler, STREAMS } from "./handlers.js";
import { createHttpHandler } from "./http.js";
import { Hub } from "./hub.js";
import { makeVerifier, remoteKeys } from "./jwt.js";
import { log } from "./log.js";
import { Presence } from "./presence.js";
import { connect } from "./redis.js";
import { StreamConsumer } from "./streams.js";

export interface App {
  server: Server;
  hub: Hub;
  /** Starts everything; resolves with the listening port. */
  start(): Promise<number>;
  stop(): Promise<void>;
  /** Periodic jobs, also run on timers. Exposed for tests. */
  sweep(): Promise<void>;
  cleanup(): Promise<void>;
}

export function createApp(config: Config, options: { keys?: JWTVerifyGetKey } = {}): App {
  const redis = connect(config.redisUrl, "main");
  const streamsRedis = connect(config.redisUrl, "streams"); // blocked by XREADGROUP
  const busRedis = connect(config.redisUrl, "bus"); // subscribed
  const db = new pg.Pool({ connectionString: config.databaseUrl, max: 10 });

  const verify = makeVerifier(options.keys ?? remoteKeys(config.authJwksUrl));
  const presence = new Presence(redis, config.presenceTtlSeconds);
  const bus = new Bus(redis, busRedis);
  const announcer = new Announcer(redis, presence, bus, config.eventsStreamMaxlen);
  const friendMessages = new FriendMessages(db);
  const partyChat = new PartyChat(redis, config.partyChatHistory);
  const hub = new Hub({ config, verify, presence, announcer, friendMessages, partyChat, bus });

  const consumer = new StreamConsumer(
    streamsRedis,
    "realtime",
    "realtime",
    STREAMS,
    createEventHandler({ bus, presence, announcer, friendMessages, partyChat }),
    { consumer: config.instanceId },
  );

  const handleHttp = createHttpHandler({ config, verify, presence, friendMessages });
  const server = createServer((req, res) => void handleHttp(req, res));
  const wss = new WebSocketServer({ noServer: true, maxPayload: 16 * 1024 });
  server.on("upgrade", (req, socket, head) => {
    if (new URL(req.url ?? "/", "http://localhost").pathname !== "/ws") return socket.destroy();
    wss.handleUpgrade(req, socket, head, (ws) => hub.accept(ws));
  });

  /** Users whose instance died never close their sockets: mark them offline after the TTL. */
  async function sweep(): Promise<void> {
    for (const userId of await presence.staleUsers()) {
      if (await presence.checkOffline(userId)) await announcer.offline(userId);
    }
  }

  /** Delete friend messages past the retention period; one instance per interval does it. */
  async function cleanup(): Promise<void> {
    const lock = await redis.set("realtime:cleanup:lock", config.instanceId, "EX", config.cleanupIntervalSeconds - 1, "NX");
    if (lock !== "OK") return;
    const deleted = await friendMessages.deleteOlderThan(config.messageRetentionDays);
    if (deleted) log("info", "deleted old friend messages", { deleted });
  }

  const timers: NodeJS.Timeout[] = [];
  const every = (seconds: number, job: () => unknown) =>
    timers.push(setInterval(() => void Promise.resolve(job()).catch((e) => log("error", "job failed", { error: String(e) })), seconds * 1000));

  return {
    server,
    hub,
    sweep,
    cleanup,
    async start() {
      await bus.start((message) => hub.onBus(message));
      void consumer.run();
      every(config.sweepIntervalSeconds, sweep);
      every(config.tokenCheckIntervalSeconds, () => hub.closeExpired());
      every(config.cleanupIntervalSeconds, cleanup);
      void cleanup().catch((e) => log("error", "cleanup failed", { error: String(e) }));
      await new Promise<void>((resolve) => server.listen(config.port, resolve));
      const port = (server.address() as AddressInfo).port;
      log("info", "real-time service listening", { port, instance: config.instanceId });
      return port;
    },
    async stop() {
      consumer.stop();
      timers.forEach(clearInterval);
      hub.closeAll();
      wss.close();
      await new Promise((resolve) => server.close(resolve));
      await Promise.allSettled([redis.quit(), streamsRedis.disconnect(), busRedis.quit(), db.end()]);
    },
  };
}
