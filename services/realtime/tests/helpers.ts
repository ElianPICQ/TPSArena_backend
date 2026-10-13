import { randomUUID } from "node:crypto";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { Redis } from "ioredis";
import { createLocalJWKSet, decodeJwt, exportJWK, generateKeyPair, SignJWT, type JWTVerifyGetKey } from "jose";
import pg from "pg";
import { inject } from "vitest";
import { WebSocket } from "ws";
import { createApp, type App } from "../src/app.js";
import { loadConfig, type Config } from "../src/config.js";
import { decode, encode, type AnyEvent } from "../src/events.js";
import type { EventDataByType, EventType } from "../src/generated/events.js";
import { EVENT_STREAMS } from "../src/generated/events.js";

// --- Tokens (stand-in for Auth) ---

const { privateKey, publicKey } = await generateKeyPair("EdDSA", { crv: "Ed25519" });
export const keys: JWTVerifyGetKey = createLocalJWKSet({
  keys: [{ ...(await exportJWK(publicKey)), kid: "test", alg: "EdDSA" }],
});

/** issuedAt: seconds since the epoch (default now). */
export async function token(userId: string, expiresIn: string | number = "15m", issuedAt?: number): Promise<string> {
  return new SignJWT({}).setProtectedHeader({ alg: "EdDSA", kid: "test" }).setSubject(userId)
    .setIssuedAt(issuedAt).setExpirationTime(expiresIn).sign(privateKey);
}

// --- Fake Social: GET /friends ---

export const friendships = new Map<string, Set<string>>();
export const social = { down: false };

export function befriend(a: string, b: string): void {
  for (const [x, y] of [[a, b], [b, a]] as const) {
    if (!friendships.has(x)) friendships.set(x, new Set());
    friendships.get(x)!.add(y);
  }
}

let socialServer: Server | undefined;
export async function socialUrl(): Promise<string> {
  if (!socialServer) {
    socialServer = createServer((req, res) => {
      if (social.down) return res.writeHead(503).end();
      const userId = decodeJwt((req.headers.authorization ?? "").slice(7)).sub!;
      const friends = [...(friendships.get(userId) ?? [])].map((user_id) => ({ user_id, since: "2026-10-13T00:00:00Z" }));
      res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({ friends, incoming: [], outgoing: [] }));
    });
    await new Promise<void>((resolve) => socialServer!.listen(0, resolve));
  }
  return `http://localhost:${(socialServer.address() as AddressInfo).port}`;
}

// --- App instances ---

export interface Instance {
  app: App;
  port: number;
  url: string;
}

const running: Instance[] = [];

export async function startInstance(overrides: Partial<Config> = {}): Promise<Instance> {
  const config = loadConfig({
    port: 0,
    redisUrl: inject("redisUrl"),
    databaseUrl: inject("databaseUrl"),
    socialUrl: await socialUrl(),
    authTimeoutSeconds: 0.5,
    offlineGraceSeconds: 0.3,
    ...overrides,
  });
  const app = createApp(config, { keys });
  const port = await app.start();
  const instance = { app, port, url: `http://localhost:${port}` };
  running.push(instance);
  return instance;
}

export async function stopInstances(): Promise<void> {
  await Promise.all(running.splice(0).map((i) => i.app.stop()));
}

// --- WebSocket client ---

export class Client {
  readonly messages: Record<string, unknown>[] = [];
  private waiters: (() => void)[] = [];
  closed: { code: number; reason: string } | null = null;

  constructor(readonly ws: WebSocket) {
    ws.on("message", (raw) => {
      this.messages.push(JSON.parse(raw.toString()));
      this.waiters.splice(0).forEach((w) => w());
    });
    ws.on("close", (code, reason) => {
      this.closed = { code, reason: reason.toString() };
      this.waiters.splice(0).forEach((w) => w());
    });
  }

  static async open(instance: Instance): Promise<Client> {
    const ws = new WebSocket(`ws://localhost:${instance.port}/ws`);
    await new Promise((resolve, reject) => ws.once("open", resolve).once("error", reject));
    return new Client(ws);
  }

  /** Connect and authenticate; resolves with the hello message. */
  static async login(instance: Instance, userId: string, accessToken?: string): Promise<Client> {
    const client = await Client.open(instance);
    client.send({ type: "auth", token: accessToken ?? (await token(userId)) });
    await client.next((m) => m.type === "hello");
    return client;
  }

  send(message: object): void {
    this.ws.send(JSON.stringify(message));
  }

  /** The first message (not yet taken) matching, waiting for it if needed. */
  async next(match: (m: Record<string, unknown>) => boolean, timeoutMs = 3000): Promise<Record<string, any>> {
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      const index = this.messages.findIndex(match);
      if (index >= 0) return this.messages.splice(index, 1)[0]!;
      if (this.closed) throw new Error(`socket closed (${this.closed.code}) while waiting`);
      const left = deadline - Date.now();
      if (left <= 0) throw new Error(`no matching message; got ${JSON.stringify(this.messages)}`);
      await new Promise<void>((resolve) => {
        this.waiters.push(resolve);
        setTimeout(resolve, left);
      });
    }
  }

  /** Waits a bit and checks nothing matching arrived. */
  async nothing(match: (m: Record<string, unknown>) => boolean, waitMs = 400): Promise<void> {
    await new Promise((r) => setTimeout(r, waitMs));
    const found = this.messages.find(match);
    if (found) throw new Error(`unexpected message ${JSON.stringify(found)}`);
  }

  async waitClosed(timeoutMs = 3000): Promise<{ code: number; reason: string }> {
    const deadline = Date.now() + timeoutMs;
    while (!this.closed && Date.now() < deadline) await new Promise((r) => setTimeout(r, 20));
    if (!this.closed) throw new Error("socket still open");
    return this.closed;
  }

  close(): void {
    this.ws.close();
  }
}

// --- Data access for assertions ---

export function testRedis(): Redis {
  return new Redis(inject("redisUrl"));
}

export function testDb(): pg.Pool {
  return new pg.Pool({ connectionString: inject("databaseUrl") });
}

export async function publishEvent<T extends EventType>(redis: Redis, type: T, data: EventDataByType[T]): Promise<void> {
  await redis.xadd(EVENT_STREAMS[type], "*", ...encode(type, data));
}

/** Events of a type published since `sinceId`, decoded. */
export async function streamEvents(redis: Redis, stream: string, sinceId = "-"): Promise<AnyEvent[]> {
  const entries = await redis.xrange(stream, sinceId, "+");
  return entries.map(([, fields]) => {
    const object: Record<string, string> = {};
    for (let i = 0; i < fields.length; i += 2) object[fields[i]!] = fields[i + 1]!;
    return decode(object);
  });
}

export const newId = () => randomUUID();
export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
