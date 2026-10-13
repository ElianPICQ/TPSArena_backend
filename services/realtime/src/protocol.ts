// WebSocket messages (JSON). See PROTOCOL.md for the client side.
import type { FriendMessage, PartyMessage } from "./chat.js";
import type { PresenceInfo } from "./presence.js";

export type ServerMessage =
  | { type: "hello"; user_id: string; heartbeat_interval: number; friends: PresenceInfo[] }
  | ({ type: "presence" } & PresenceInfo)
  | { type: "friend_message"; message: FriendMessage }
  | { type: "friend_message_sent"; client_id: string | null; message: FriendMessage }
  | { type: "party_message"; message: PartyMessage }
  | { type: "party_history"; party_id: string; messages: PartyMessage[] }
  | { type: "notification"; event: string; data: unknown }
  | { type: "error"; error: string; message: string };

export const CloseCode = {
  authFailed: 4001, // missing, invalid, expired or revoked token
  accountDeleted: 4003,
  authTimeout: 4008, // no `auth` message in time
  tryAgainLater: 1013, // a service we depend on (Social) is unavailable
} as const;
