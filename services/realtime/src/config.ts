import { randomUUID } from "node:crypto";

export interface Config {
  port: number;
  redisUrl: string;
  databaseUrl: string;
  authJwksUrl: string;
  socialUrl: string;
  /** Unique per process: connection ids and the stream consumer name. */
  instanceId: string;

  authTimeoutSeconds: number; // a new socket must send `auth` within this time
  tokenCheckIntervalSeconds: number; // how often expired tokens are looked for
  heartbeatIntervalSeconds: number; // told to clients in `hello`
  presenceTtlSeconds: number; // a connection without heartbeat for this long is gone
  offlineGraceSeconds: number; // after the last socket closes, before "offline"
  sweepIntervalSeconds: number; // looks for users of crashed instances
  accessTokenTtlSeconds: number; // Auth's access token lifetime: how long a revocation is remembered

  messageMaxLength: number;
  messageRateLimit: number; // friend + party messages per window, per connection
  messageRateWindowSeconds: number;
  partyChatHistory: number;
  messageRetentionDays: number;
  cleanupIntervalSeconds: number;
  eventsStreamMaxlen: number;
}

function env(name: string, fallback: string): string {
  return process.env[name] ?? fallback;
}

export function loadConfig(overrides: Partial<Config> = {}): Config {
  return {
    port: Number(env("PORT", "8005")),
    redisUrl: env("REDIS_URL", "redis://localhost:6379/0"),
    databaseUrl: env("DATABASE_URL", "postgres://realtime_svc:realtime_dev_password@localhost:5432/realtime"),
    authJwksUrl: env("AUTH_JWKS_URL", "http://localhost:8001/.well-known/jwks.json"),
    socialUrl: env("SOCIAL_URL", "http://localhost:8003"),
    instanceId: randomUUID().slice(0, 8),
    authTimeoutSeconds: 5,
    tokenCheckIntervalSeconds: 30,
    heartbeatIntervalSeconds: 20,
    presenceTtlSeconds: 60,
    offlineGraceSeconds: 30,
    sweepIntervalSeconds: 15,
    accessTokenTtlSeconds: Number(env("ACCESS_TOKEN_TTL_SECONDS", "900")),
    messageMaxLength: 500,
    messageRateLimit: 10,
    messageRateWindowSeconds: 10,
    partyChatHistory: 100,
    messageRetentionDays: 90,
    cleanupIntervalSeconds: 3600,
    eventsStreamMaxlen: 100_000,
    ...overrides,
  };
}
