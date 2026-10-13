// What each stream event does. A stream event reaches one instance (one consumer group); pushes
// go through the bus so the players' own instances deliver them.
import type { Announcer } from "./announcer.js";
import type { Bus } from "./bus.js";
import type { FriendMessages, PartyChat } from "./chat.js";
import type { AnyEvent } from "./events.js";
import type { Presence } from "./presence.js";
import { CloseCode } from "./protocol.js";

export const STREAMS = ["social.events", "stats.progress", "player.match", "user.deleted"];

export interface HandlerDeps {
  bus: Bus;
  presence: Presence;
  announcer: Announcer;
  friendMessages: FriendMessages;
  partyChat: PartyChat;
}

export function createEventHandler(deps: HandlerDeps): (event: AnyEvent) => Promise<void> {
  const { bus, presence, announcer, friendMessages, partyChat } = deps;

  const notify = (userIds: string[], event: AnyEvent, data: unknown = event.data) =>
    bus.publish({ kind: "push", userIds, payload: { type: "notification", event: event.type, data } });

  return async (event) => {
    switch (event.type) {
      case "friend.request_sent":
        return notify([event.data.to_user_id], event);
      case "friend.request_removed":
        return notify([event.data.from_user_id, event.data.to_user_id], event);
      case "friend.added":
      case "friend.removed": {
        const { user_id: a, friend_id: b } = event.data;
        await bus.publish({ kind: "friendship", a, b, op: event.type === "friend.added" ? "add" : "remove" });
        return notify([a, b], event);
      }
      case "party.invite_sent":
        return notify([event.data.to_user_id], event);
      case "party.updated": {
        const { party_id, version, member_ids } = event.data;
        const previous = await partyChat.applySnapshot(party_id, version, member_ids);
        if (previous === null) return; // an older snapshot arriving late
        // Removed members (left, kicked) learn it too.
        return notify([...new Set([...previous, ...member_ids])], event);
      }
      case "party.match_ready": {
        const { tickets, ...match } = event.data;
        // Each member gets only their own ticket.
        for (const [userId, ticket] of Object.entries(tickets)) await notify([userId], event, { ...match, ticket });
        return;
      }
      case "party.disbanded": {
        const previous = await partyChat.disband(event.data.party_id);
        return notify(previous, event);
      }
      case "progress.level_up":
      case "progress.achievement_unlocked":
        return notify([event.data.user_id], event);
      case "player.match": {
        const activity = event.data.action === "joined" ? "in_match" : "menu";
        if (await presence.setActivity(event.data.user_id, activity)) await announcer.changed(event.data.user_id);
        return;
      }
      case "user.deleted": {
        const userId = event.data.user_id;
        await friendMessages.deleteUser(userId);
        const wasOnline = await presence.remove(userId);
        await bus.publish({ kind: "close", userId, code: CloseCode.accountDeleted, reason: "account deleted" });
        if (wasOnline) {
          await bus.publish({ kind: "presence", presence: { user_id: userId, status: "offline", activity: null } });
        }
        return;
      }
      default:
        return; // events of these streams that Real-time doesn't use
    }
  };
}
