// Sockets connected to this instance and the WebSocket protocol (see PROTOCOL.md).
import { WebSocket, type RawData } from "ws";
import type { Announcer } from "./announcer.js";
import type { Bus, BusMessage } from "./bus.js";
import type { FriendMessages, PartyChat } from "./chat.js";
import type { Config } from "./config.js";
import { TokenError, type Verifier } from "./jwt.js";
import { log } from "./log.js";
import type { ClientKind, Presence } from "./presence.js";
import { CloseCode, type ServerMessage } from "./protocol.js";
import { issuedBefore, type Revocations } from "./revocations.js";
import { fetchFriends, SocialUnavailable } from "./social.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export interface HubDeps {
  config: Config;
  verify: Verifier;
  presence: Presence;
  announcer: Announcer;
  friendMessages: FriendMessages;
  partyChat: PartyChat;
  bus: Bus;
  revocations: Revocations;
}

export class Connection {
  userId: string | null = null;
  kind: ClientKind = "game";
  token = "";
  expiresAt = 0;
  issuedAt = 0;
  friends = new Set<string>();
  private queue: Promise<void> = Promise.resolve();
  private sentAt: number[] = [];

  constructor(
    readonly id: string,
    readonly ws: WebSocket,
  ) {}

  send(message: ServerMessage): void {
    if (this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(message));
  }

  error(error: string, message: string): void {
    this.send({ type: "error", error, message });
  }

  /** Messages from one socket are handled one after the other, in order. */
  enqueue(task: () => Promise<void>): void {
    this.queue = this.queue
      .then(task)
      .catch((error) => log("error", "socket task failed", { connection: this.id, error: String(error) }));
  }

  allowMessage(limit: number, windowMs: number): boolean {
    const now = Date.now();
    this.sentAt = this.sentAt.filter((t) => now - t < windowMs);
    if (this.sentAt.length >= limit) return false;
    this.sentAt.push(now);
    return true;
  }
}

function addTo<K, V>(map: Map<K, Set<V>>, key: K, value: V): void {
  let set = map.get(key);
  if (!set) map.set(key, (set = new Set()));
  set.add(value);
}

function removeFrom<K, V>(map: Map<K, Set<V>>, key: K, value: V): void {
  const set = map.get(key);
  set?.delete(value);
  if (set?.size === 0) map.delete(key);
}

export class Hub {
  private readonly byUser = new Map<string, Set<Connection>>();
  private readonly byFriend = new Map<string, Set<Connection>>(); // user id -> sockets of their friends
  private readonly offlineTimers = new Set<NodeJS.Timeout>();
  private nextId = 0;
  private stopping = false;

  constructor(private readonly deps: HubDeps) {}

  get connectionCount(): number {
    let count = 0;
    for (const set of this.byUser.values()) count += set.size;
    return count;
  }

  accept(ws: WebSocket): void {
    const { config } = this.deps;
    const conn = new Connection(`${config.instanceId}-${++this.nextId}`, ws);
    const authTimer = setTimeout(() => {
      if (!conn.userId) ws.close(CloseCode.authTimeout, "auth timeout");
    }, config.authTimeoutSeconds * 1000);

    ws.on("message", (raw) => conn.enqueue(() => this.onMessage(conn, raw)));
    ws.on("close", () => {
      clearTimeout(authTimer);
      conn.enqueue(() => this.onClose(conn));
    });
    ws.on("error", (error) => log("warn", "socket error", { connection: conn.id, error: String(error) }));
  }

  // --- Client messages ---

  private async onMessage(conn: Connection, raw: RawData): Promise<void> {
    let message: Record<string, unknown>;
    try {
      message = JSON.parse(raw.toString());
      if (typeof message !== "object" || message === null) throw new Error();
    } catch {
      return conn.error("bad_message", "Messages must be JSON objects.");
    }
    if (message.type !== "auth" && !conn.userId) return conn.error("not_authenticated", "Send `auth` first.");
    try {
      switch (message.type) {
        case "auth":
          return await this.authenticate(conn, String(message.token ?? ""), message.client);
        case "heartbeat":
          return await this.heartbeat(conn);
        case "friend_message":
          return await this.friendMessage(conn, message);
        case "party_message":
          return await this.partyMessage(conn, message);
        case "party_history":
          return await this.partyHistory(conn, message);
        default:
          return conn.error("unknown_type", `Unknown message type: ${String(message.type)}`);
      }
    } catch (error) {
      log("error", "message handling failed", { connection: conn.id, type: String(message.type), error: String(error) });
      conn.error("internal_error", "Something went wrong, try again.");
    }
  }

  /** `client`: "mobile" for the companion app; anything else (or nothing) is the game. Set on the first auth only. */
  private async authenticate(conn: Connection, token: string, client?: unknown): Promise<void> {
    const { verify, config, presence, friendMessages, announcer, revocations } = this.deps;
    let verified;
    try {
      verified = await verify(token);
      if (await revocations.isRevoked(verified.userId, verified.issuedAt)) {
        throw new TokenError("session_revoked", "This session was ended (password reset), log in again.");
      }
    } catch (error) {
      if (!(error instanceof TokenError)) throw error;
      conn.error(error.code, error.message);
      conn.ws.close(CloseCode.authFailed, error.code);
      return;
    }

    // A refreshed token for the same player keeps the connection alive past the old expiry.
    if (conn.userId) {
      if (verified.userId !== conn.userId) {
        conn.error("invalid_token", "This token belongs to another player.");
        conn.ws.close(CloseCode.authFailed, "invalid_token");
        return;
      }
      conn.token = token;
      conn.expiresAt = verified.expiresAt;
      conn.issuedAt = verified.issuedAt;
      return;
    }

    let friends: string[];
    try {
      friends = await fetchFriends(config.socialUrl, token);
    } catch (error) {
      if (error instanceof TokenError) {
        conn.error(error.code, error.message);
        conn.ws.close(CloseCode.authFailed, error.code);
      } else if (error instanceof SocialUnavailable) {
        conn.error("service_unavailable", "Can't load your friends right now, reconnect in a moment.");
        conn.ws.close(CloseCode.tryAgainLater, "social unavailable");
      } else throw error;
      return;
    }
    if (conn.ws.readyState !== WebSocket.OPEN) return; // closed while we were asking Social

    conn.userId = verified.userId;
    conn.kind = client === "mobile" ? "mobile" : "game";
    conn.token = token;
    conn.expiresAt = verified.expiresAt;
    conn.issuedAt = verified.issuedAt;
    addTo(this.byUser, conn.userId, conn);
    for (const friend of friends) this.addFriend(conn, friend);

    const change = await presence.touch(conn.userId, conn.id, conn.kind);
    conn.send({
      type: "hello",
      user_id: conn.userId,
      heartbeat_interval: config.heartbeatIntervalSeconds,
      friends: await presence.get(friends),
    });
    await announcer.touched(conn.userId, change);

    // Messages sent while the player was offline.
    const waiting = await friendMessages.undelivered(conn.userId);
    for (const message of waiting) conn.send({ type: "friend_message", message });
    await friendMessages.markDelivered(waiting.map((m) => m.id));
  }

  private async heartbeat(conn: Connection): Promise<void> {
    const change = await this.deps.presence.touch(conn.userId!, conn.id, conn.kind);
    await this.deps.announcer.touched(conn.userId!, change);
  }

  private checkBody(conn: Connection, body: unknown): string | null {
    const { config } = this.deps;
    const text = typeof body === "string" ? body.trim() : "";
    if (text.length === 0 || text.length > config.messageMaxLength) {
      conn.error("invalid_body", `A message has 1 to ${config.messageMaxLength} characters.`);
      return null;
    }
    if (!conn.allowMessage(config.messageRateLimit, config.messageRateWindowSeconds * 1000)) {
      conn.error("rate_limited", "You are sending messages too fast.");
      return null;
    }
    return text;
  }

  private async friendMessage(conn: Connection, message: Record<string, unknown>): Promise<void> {
    const to = String(message.to ?? "").toLowerCase();
    const clientId = typeof message.client_id === "string" ? message.client_id : null;
    if (!UUID.test(to)) return conn.error("invalid_recipient", "`to` must be a user id.");
    if (!conn.friends.has(to)) return conn.error("not_friends", "You can only message your friends.");
    const body = this.checkBody(conn, message.body);
    if (body === null) return;

    const saved = await this.deps.friendMessages.send(conn.userId!, to, body);
    conn.send({ type: "friend_message_sent", client_id: clientId, message: saved });
    await this.deps.bus.publish({ kind: "push", userIds: [to], payload: { type: "friend_message", message: saved } });
  }

  private async partyMessage(conn: Connection, message: Record<string, unknown>): Promise<void> {
    const partyId = String(message.party_id ?? "");
    if (!(await this.deps.partyChat.isMember(partyId, conn.userId!))) {
      return conn.error("not_in_party", "You aren't in this party.");
    }
    const body = this.checkBody(conn, message.body);
    if (body === null) return;
    const saved = { party_id: partyId, from_user_id: conn.userId!, body, sent_at: new Date().toISOString() };
    await this.deps.partyChat.post(saved);
    const members = await this.deps.partyChat.members(partyId);
    await this.deps.bus.publish({ kind: "push", userIds: members, payload: { type: "party_message", message: saved } });
  }

  private async partyHistory(conn: Connection, message: Record<string, unknown>): Promise<void> {
    const partyId = String(message.party_id ?? "");
    if (!(await this.deps.partyChat.isMember(partyId, conn.userId!))) {
      return conn.error("not_in_party", "You aren't in this party.");
    }
    conn.send({ type: "party_history", party_id: partyId, messages: await this.deps.partyChat.history(partyId) });
  }

  private async onClose(conn: Connection): Promise<void> {
    const userId = conn.userId;
    // While this instance shuts down, other instances' sweeper marks its players offline.
    if (!userId || this.stopping) return;
    removeFrom(this.byUser, userId, conn);
    for (const friend of conn.friends) removeFrom(this.byFriend, friend, conn);
    await this.deps.presence.dropConnection(userId, conn.id);
    // Offline (or game closed) only if no other socket (here or on another instance) shows up
    // within the grace period.
    const timer = setTimeout(() => {
      this.offlineTimers.delete(timer);
      void this.deps.presence
        .checkOffline(userId)
        .then((change) => this.deps.announcer.checked(userId, change))
        .catch((error) => log("error", "offline check failed", { userId, error: String(error) }));
    }, this.deps.config.offlineGraceSeconds * 1000);
    this.offlineTimers.add(timer);
  }

  private addFriend(conn: Connection, friendId: string): void {
    conn.friends.add(friendId);
    addTo(this.byFriend, friendId, conn);
  }

  private removeFriend(conn: Connection, friendId: string): void {
    conn.friends.delete(friendId);
    removeFrom(this.byFriend, friendId, conn);
  }

  // --- Messages from the bus (any instance) ---

  async onBus(message: BusMessage): Promise<void> {
    switch (message.kind) {
      case "push": {
        let delivered = false;
        for (const userId of message.userIds) {
          for (const conn of this.byUser.get(userId) ?? []) {
            conn.send(message.payload);
            delivered = true;
          }
        }
        if (delivered && message.payload.type === "friend_message") {
          await this.deps.friendMessages.markDelivered([message.payload.message.id]);
        }
        return;
      }
      case "presence":
        for (const conn of this.byFriend.get(message.presence.user_id) ?? []) {
          conn.send({ type: "presence", ...message.presence });
        }
        return;
      case "friendship": {
        const pairs: [string, string][] = [[message.a, message.b], [message.b, message.a]];
        for (const [userId, friendId] of pairs) {
          const conns = this.byUser.get(userId);
          if (!conns) continue;
          if (message.op === "add") {
            const [presence] = await this.deps.presence.get([friendId]);
            for (const conn of conns) {
              this.addFriend(conn, friendId);
              conn.send({ type: "presence", ...presence! });
            }
          } else {
            for (const conn of conns) this.removeFriend(conn, friendId);
          }
        }
        return;
      }
      case "close":
        for (const conn of this.byUser.get(message.userId) ?? []) {
          if (message.issuedBefore !== undefined && !issuedBefore(conn.issuedAt, message.issuedBefore)) continue;
          conn.ws.close(message.code, message.reason);
        }
        return;
    }
  }

  // --- Periodic jobs ---

  /** Close sockets whose token expired without the client sending a refreshed one. */
  closeExpired(): void {
    const now = Date.now();
    for (const conns of this.byUser.values()) {
      for (const conn of conns) {
        if (now >= conn.expiresAt) {
          conn.error("token_expired", "Send a refreshed token with `auth` before it expires.");
          conn.ws.close(CloseCode.authFailed, "token_expired");
        }
      }
    }
  }

  closeAll(): void {
    this.stopping = true;
    for (const timer of this.offlineTimers) clearTimeout(timer);
    this.offlineTimers.clear();
    for (const conns of this.byUser.values()) for (const conn of conns) conn.ws.close(1001, "server shutting down");
  }
}
