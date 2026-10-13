// HTTP routes (reached through the BFF): GET /health, GET /presence, GET /conversations/{id}/messages.
import type { IncomingMessage, ServerResponse } from "node:http";
import type { FriendMessages } from "./chat.js";
import type { Config } from "./config.js";
import { TokenError, type Verifier } from "./jwt.js";
import { log } from "./log.js";
import type { Presence } from "./presence.js";
import { fetchFriends, SocialUnavailable } from "./social.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

class HttpError extends Error {
  constructor(
    readonly status: number,
    readonly error: string,
    message: string,
  ) {
    super(message);
  }
}

function send(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

export interface HttpDeps {
  config: Config;
  verify: Verifier;
  presence: Presence;
  friendMessages: FriendMessages;
}

export function createHttpHandler(deps: HttpDeps) {
  const { config, verify, presence, friendMessages } = deps;

  async function caller(req: IncomingMessage): Promise<{ userId: string; token: string }> {
    const header = req.headers.authorization ?? "";
    const token = header.startsWith("Bearer ") ? header.slice(7) : "";
    try {
      return { userId: (await verify(token)).userId, token };
    } catch (error) {
      if (error instanceof TokenError) throw new HttpError(401, error.code, error.message);
      throw error;
    }
  }

  async function getPresence(req: IncomingMessage, url: URL) {
    const { token } = await caller(req);
    const ids = (url.searchParams.get("ids") ?? "").split(",").map((i) => i.trim().toLowerCase()).filter(Boolean);
    if (ids.length > 100) throw new HttpError(400, "too_many_ids", "At most 100 ids per request.");
    if (ids.some((i) => !UUID.test(i))) throw new HttpError(400, "invalid_ids", "ids must be user ids.");
    let friends: Set<string>;
    try {
      friends = new Set(await fetchFriends(config.socialUrl, token));
    } catch (error) {
      if (error instanceof SocialUnavailable) throw new HttpError(503, "social_unavailable", "Try again shortly.");
      throw error;
    }
    // Only friends can see a player's status: other ids are left out.
    return { presence: await presence.get([...new Set(ids)].filter((i) => friends.has(i))) };
  }

  async function getConversation(req: IncomingMessage, url: URL, friendId: string) {
    const { userId } = await caller(req);
    if (!UUID.test(friendId)) throw new HttpError(400, "invalid_user", "Not a user id.");
    const limit = Number(url.searchParams.get("limit") ?? 20);
    if (!Number.isInteger(limit) || limit < 1 || limit > 50) throw new HttpError(400, "invalid_limit", "limit is 1 to 50.");
    const before = url.searchParams.get("before") ?? undefined;
    if (before !== undefined && !/^\d{1,18}$/.test(before)) {
      throw new HttpError(400, "invalid_cursor", "Use next_cursor from the previous page.");
    }
    const rows = await friendMessages.conversation(userId, friendId.toLowerCase(), limit + 1, before);
    const messages = rows.slice(0, limit);
    return { messages, next_cursor: rows.length > limit ? messages.at(-1)!.id : null };
  }

  return async (req: IncomingMessage, res: ServerResponse): Promise<void> => {
    const url = new URL(req.url ?? "/", "http://localhost");
    try {
      if (req.method !== "GET") throw new HttpError(405, "method_not_allowed", "Only GET.");
      if (url.pathname === "/health") return send(res, 200, { status: "ok" });
      if (url.pathname === "/presence") return send(res, 200, await getPresence(req, url));
      const conversation = url.pathname.match(/^\/conversations\/([^/]+)\/messages$/);
      if (conversation) return send(res, 200, await getConversation(req, url, decodeURIComponent(conversation[1]!)));
      throw new HttpError(404, "not_found", "No such route.");
    } catch (error) {
      if (error instanceof HttpError) return send(res, error.status, { error: error.error, message: error.message });
      log("error", "request failed", { path: url.pathname, error: String(error) });
      send(res, 500, { error: "internal_error", message: "Something went wrong." });
    }
  };
}
