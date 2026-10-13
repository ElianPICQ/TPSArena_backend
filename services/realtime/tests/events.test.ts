import { afterAll, afterEach, describe, expect, it } from "vitest";
import { befriend, Client, newId, publishEvent, sleep, startInstance, stopInstances, testDb, testRedis } from "./helpers.js";

const redis = testRedis();
const db = testDb();
afterEach(stopInstances);
afterAll(async () => {
  await redis.quit();
  await db.end();
});

const notification = (event: string) => (m: Record<string, unknown>) => m.type === "notification" && m.event === event;

describe("social events", () => {
  it("friend requests reach the players concerned", async () => {
    const instance = await startInstance();
    const [alice, bob] = [newId(), newId()];
    const a = await Client.login(instance, alice);
    const b = await Client.login(instance, bob);
    const request = { request_id: newId(), from_user_id: alice, to_user_id: bob };

    await publishEvent(redis, "friend.request_sent", request);
    expect((await b.next(notification("friend.request_sent"))).data).toEqual(request);
    await a.nothing(notification("friend.request_sent"));

    await publishEvent(redis, "friend.request_removed", request);
    await a.next(notification("friend.request_removed"));
    await b.next(notification("friend.request_removed"));
  });

  it("a new friendship shows presence and allows messages right away; removing it stops both", async () => {
    const [one, two] = [await startInstance(), await startInstance()];
    const [alice, bob] = [newId(), newId()];
    const a = await Client.login(one, alice);
    const b = await Client.login(two, bob);

    await publishEvent(redis, "friend.added", { user_id: alice, friend_id: bob });
    await a.next(notification("friend.added"));
    expect(await a.next((m) => m.type === "presence")).toMatchObject({ user_id: bob, status: "online" });
    expect(await b.next((m) => m.type === "presence")).toMatchObject({ user_id: alice, status: "online" });
    a.send({ type: "friend_message", to: bob, body: "now we're friends" });
    await b.next((m) => m.type === "friend_message");

    await publishEvent(redis, "friend.removed", { user_id: alice, friend_id: bob });
    await b.next(notification("friend.removed"));
    a.send({ type: "friend_message", to: bob, body: "still there?" });
    expect((await a.next((m) => m.type === "error")).error).toBe("not_friends");
  });

  it("party invites go to the invited player", async () => {
    const instance = await startInstance();
    const [alice, bob] = [newId(), newId()];
    const b = await Client.login(instance, bob);
    await publishEvent(redis, "party.invite_sent", {
      invite_id: "inv1", party_id: newId(), from_user_id: alice, to_user_id: bob, expires_at: new Date().toISOString(),
    });
    expect((await b.next(notification("party.invite_sent"))).data.invite_id).toBe("inv1");
  });
});

describe("party chat", () => {
  it("follows party.updated: members chat, removed members are told and lose access", async () => {
    const instance = await startInstance();
    const [alice, bob, outsider] = [newId(), newId(), newId()];
    const party = newId();
    const a = await Client.login(instance, alice);
    const b = await Client.login(instance, bob);
    const o = await Client.login(instance, outsider);

    const snapshot = (version: number, members: string[]) =>
      publishEvent(redis, "party.updated", { party_id: party, version, leader_id: alice, status: "lobby", member_ids: members });

    await snapshot(2, [alice, bob]);
    await a.next(notification("party.updated"));
    await b.next(notification("party.updated"));

    a.send({ type: "party_message", party_id: party, body: "ready?" });
    expect((await b.next((m) => m.type === "party_message")).message).toMatchObject({ from_user_id: alice, body: "ready?" });
    await a.next((m) => m.type === "party_message"); // the sender's other devices get it too

    o.send({ type: "party_message", party_id: party, body: "let me in" });
    expect((await o.next((m) => m.type === "error")).error).toBe("not_in_party");

    // A late, older snapshot is ignored.
    await snapshot(1, [alice, bob, outsider]);
    await o.nothing(notification("party.updated"));

    // Bob is kicked: he's told, and can't read or post any more.
    await snapshot(3, [alice]);
    await b.next(notification("party.updated"));
    b.send({ type: "party_history", party_id: party });
    expect((await b.next((m) => m.type === "error")).error).toBe("not_in_party");

    a.send({ type: "party_history", party_id: party });
    expect((await a.next((m) => m.type === "party_history")).messages.map((m: any) => m.body)).toEqual(["ready?"]);

    await publishEvent(redis, "party.disbanded", { party_id: party });
    await a.next(notification("party.disbanded"));
    await sleep(100);
    expect(await redis.exists(`realtime:party_chat:${party}`, `realtime:party_chat:${party}:members`)).toBe(0);
  });

  it("match_ready gives each member only their own ticket", async () => {
    const instance = await startInstance();
    const [alice, bob] = [newId(), newId()];
    const a = await Client.login(instance, alice);
    const b = await Client.login(instance, bob);
    await publishEvent(redis, "party.match_ready", {
      party_id: newId(), server_id: newId(), address: "203.0.113.10", port: 7000,
      tickets: { [alice]: "ticket-a", [bob]: "ticket-b" },
    });
    const forA = (await a.next(notification("party.match_ready"))).data;
    expect(forA).toMatchObject({ address: "203.0.113.10", port: 7000, ticket: "ticket-a" });
    expect(forA).not.toHaveProperty("tickets");
    expect((await b.next(notification("party.match_ready"))).data.ticket).toBe("ticket-b");
  });
});

describe("other events", () => {
  it("level ups and achievements reach the player", async () => {
    const instance = await startInstance();
    const alice = newId();
    const a = await Client.login(instance, alice);
    await publishEvent(redis, "progress.level_up", { user_id: alice, level: 2, match_id: newId() });
    await publishEvent(redis, "progress.achievement_unlocked", { user_id: alice, achievement_id: "first_win", match_id: newId() });
    expect((await a.next(notification("progress.level_up"))).data.level).toBe(2);
    expect((await a.next(notification("progress.achievement_unlocked"))).data.achievement_id).toBe("first_win");
  });

  it("player.match switches friends' view to in match and back", async () => {
    const instance = await startInstance();
    const [alice, bob] = [newId(), newId()];
    befriend(alice, bob);
    const a = await Client.login(instance, alice);
    await Client.login(instance, bob);
    await a.next((m) => m.type === "presence" && m.status === "online");

    await publishEvent(redis, "player.match", { user_id: bob, server_id: newId(), action: "joined" });
    expect(await a.next((m) => m.type === "presence")).toMatchObject({ user_id: bob, activity: "in_match" });
    await publishEvent(redis, "player.match", { user_id: bob, server_id: newId(), action: "left" });
    expect(await a.next((m) => m.type === "presence")).toMatchObject({ user_id: bob, activity: "menu" });
  });

  it("user.deleted closes the player's sockets and deletes their messages", async () => {
    const instance = await startInstance();
    const [alice, bob] = [newId(), newId()];
    befriend(alice, bob);
    const a = await Client.login(instance, alice);
    const b = await Client.login(instance, bob);
    a.send({ type: "friend_message", to: bob, body: "bye" });
    await a.next((m) => m.type === "friend_message_sent");
    await a.next((m) => m.type === "presence" && m.status === "online");

    await publishEvent(redis, "user.deleted", { user_id: bob });
    expect((await b.waitClosed()).code).toBe(4003);
    expect(await a.next((m) => m.type === "presence")).toMatchObject({ user_id: bob, status: "offline" });
    const { rows } = await db.query("SELECT count(*)::int AS n FROM chat.messages WHERE to_user_id = $1", [bob]);
    expect(rows[0].n).toBe(0);
  });

  it("an undecodable event is dropped without blocking the stream", async () => {
    const instance = await startInstance();
    const alice = newId();
    const a = await Client.login(instance, alice);
    await redis.xadd("stats.progress", "*", "event_id", newId(), "type", "progress.level_up", "data", '{"user_id": 42}');
    await publishEvent(redis, "progress.level_up", { user_id: alice, level: 3, match_id: newId() });
    expect((await a.next(notification("progress.level_up"))).data.level).toBe(3);
    await sleep(100);
    expect((await redis.xpending("stats.progress", "realtime"))[0]).toBe(0);
  });
});
