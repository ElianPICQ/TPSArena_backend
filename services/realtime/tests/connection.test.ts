import { afterAll, afterEach, describe, expect, it } from "vitest";
import { befriend, Client, newId, sleep, social, startInstance, stopInstances, streamEvents, testRedis, token } from "./helpers.js";

const redis = testRedis();
afterEach(stopInstances);
afterAll(() => redis.quit());

describe("authentication", () => {
  it("closes a socket that never authenticates", async () => {
    const client = await Client.open(await startInstance());
    expect((await client.waitClosed()).code).toBe(4008);
  });

  it("refuses other messages before auth", async () => {
    const client = await Client.open(await startInstance());
    client.send({ type: "heartbeat" });
    expect((await client.next((m) => m.type === "error")).error).toBe("not_authenticated");
  });

  it("rejects an invalid or expired token", async () => {
    const instance = await startInstance();
    const bad = await Client.open(instance);
    bad.send({ type: "auth", token: "not.a.jwt" });
    expect((await bad.next((m) => m.type === "error")).error).toBe("invalid_token");
    expect((await bad.waitClosed()).code).toBe(4001);

    const expired = await Client.open(instance);
    expired.send({ type: "auth", token: await token(newId(), Math.floor(Date.now() / 1000) - 60) });
    expect((await expired.next((m) => m.type === "error")).error).toBe("token_expired");
    expect((await expired.waitClosed()).code).toBe(4001);
  });

  it("accepts a refreshed token for the same player only", async () => {
    const userId = newId();
    const client = await Client.login(await startInstance(), userId);
    client.send({ type: "auth", token: await token(userId) });
    await client.nothing((m) => m.type === "error");
    client.send({ type: "auth", token: await token(newId()) });
    expect((await client.waitClosed()).code).toBe(4001);
  });

  it("closes sockets whose token expired without a refresh", async () => {
    const instance = await startInstance();
    const userId = newId();
    const client = await Client.open(instance);
    client.send({ type: "auth", token: await token(userId, Math.floor(Date.now() / 1000) + 1) });
    await client.next((m) => m.type === "hello");
    await sleep(1100);
    instance.app.hub.closeExpired();
    expect((await client.next((m) => m.type === "error")).error).toBe("token_expired");
    expect((await client.waitClosed()).code).toBe(4001);
  });

  it("asks the client to reconnect later when Social is down", async () => {
    social.down = true;
    try {
      const client = await Client.open(await startInstance());
      client.send({ type: "auth", token: await token(newId()) });
      expect((await client.next((m) => m.type === "error")).error).toBe("service_unavailable");
      expect((await client.waitClosed()).code).toBe(1013);
    } finally {
      social.down = false;
    }
  });
});

describe("presence", () => {
  it("hello lists friends' presence; friends see each other come and go", async () => {
    const instance = await startInstance();
    const [alice, bob] = [newId(), newId()];
    befriend(alice, bob);
    const start = (await redis.xrevrange("presence.changed", "+", "-", "COUNT", 1))[0]?.[0] ?? "0";

    const a = await Client.open(instance);
    a.send({ type: "auth", token: await token(alice) });
    const hello = await a.next((m) => m.type === "hello");
    expect(hello).toMatchObject({ user_id: alice, heartbeat_interval: 20 });
    expect(hello.friends).toEqual([{ user_id: bob, status: "offline", activity: null }]);

    const b = await Client.login(instance, bob);
    expect(await a.next((m) => m.type === "presence")).toEqual({ type: "presence", user_id: bob, status: "online", activity: "menu" });

    b.close();
    expect(await a.next((m) => m.type === "presence")).toMatchObject({ user_id: bob, status: "offline" });
    const changes = (await streamEvents(redis, "presence.changed", start)).filter((e) => e.data.user_id === bob);
    expect(changes.map((e) => e.data)).toEqual([
      { user_id: bob, status: "online" },
      { user_id: bob, status: "offline" },
    ]);
  });

  it("a quick reconnect within the grace period doesn't go offline", async () => {
    const instance = await startInstance({ offlineGraceSeconds: 1 });
    const [alice, bob] = [newId(), newId()];
    befriend(alice, bob);
    const a = await Client.login(instance, alice);
    const b = await Client.login(instance, bob);
    await a.next((m) => m.type === "presence" && m.status === "online");
    b.close();
    await Client.login(instance, bob);
    await a.nothing((m) => m.type === "presence", 1300);
  });

  it("only friends see presence", async () => {
    const instance = await startInstance();
    const [alice, stranger] = [newId(), newId()];
    const s = await Client.login(instance, stranger);
    await Client.login(instance, alice);
    await s.nothing((m) => m.type === "presence");
  });

  it("works across instances, and goes offline once even with two instances", async () => {
    const [one, two] = [await startInstance(), await startInstance()];
    const [alice, bob] = [newId(), newId()];
    befriend(alice, bob);
    const a = await Client.login(one, alice);
    const b1 = await Client.login(two, bob);
    const b2 = await Client.login(one, bob); // second device
    await a.next((m) => m.type === "presence" && m.status === "online");
    b1.close();
    await a.nothing((m) => m.type === "presence", 600); // still connected on the other device
    b2.close();
    await a.next((m) => m.type === "presence" && m.status === "offline");
    await a.nothing((m) => m.type === "presence", 600);
  });

  it("the companion app shows as mobile, and only the game counts for Social", async () => {
    const instance = await startInstance();
    const [alice, bob] = [newId(), newId()];
    befriend(alice, bob);
    const a = await Client.login(instance, alice);
    const start = (await redis.xrevrange("presence.changed", "+", "-", "COUNT", 1))[0]?.[0] ?? "0";
    const changes = async () =>
      (await streamEvents(redis, "presence.changed", start)).filter((e) => e.data.user_id === bob).map((e) => e.data.status);
    const presence = (m: Record<string, unknown>) => m.type === "presence" && m.user_id === bob;

    // Phone first: online for friends, nothing for Social.
    const phone = await Client.open(instance);
    phone.send({ type: "auth", token: await token(bob), client: "mobile" });
    await phone.next((m) => m.type === "hello");
    expect(await a.next(presence)).toMatchObject({ status: "online", activity: "mobile" });
    expect(await changes()).toEqual([]);

    // The game opens: in the menu, and Social learns it.
    const game = await Client.login(instance, bob);
    expect(await a.next(presence)).toMatchObject({ status: "online", activity: "menu" });
    expect(await changes()).toEqual(["online"]);

    // The game closes: back to mobile; Social sees the player leave (removed from their party).
    game.close();
    expect(await a.next(presence)).toMatchObject({ status: "online", activity: "mobile" });
    expect(await changes()).toEqual(["online", "offline"]);

    // The phone closes too: offline.
    phone.close();
    expect(await a.next(presence)).toMatchObject({ status: "offline", activity: null });
  });

  it("the sweeper marks users of a crashed instance offline", async () => {
    const instance = await startInstance();
    const ghost = newId();
    // A connection whose instance died 2 minutes ago: last heartbeat long gone.
    const old = Date.now() - 120_000;
    await redis.zadd("realtime:presence:last_seen", old, ghost);
    await redis.zadd(`realtime:presence:${ghost}:conns`, old, "dead-1");
    const before = (await redis.xrevrange("presence.changed", "+", "-", "COUNT", 1))[0]?.[0] ?? "0";
    await instance.app.sweep();
    const events = (await streamEvents(redis, "presence.changed", before)).filter((e) => e.data.user_id === ghost);
    expect(events.map((e) => e.data.status)).toEqual(["offline"]);
    await instance.app.sweep();
    expect((await streamEvents(redis, "presence.changed", before)).filter((e) => e.data.user_id === ghost)).toHaveLength(1);
  });
});
