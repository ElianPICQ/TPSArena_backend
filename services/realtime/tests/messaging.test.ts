import { afterAll, afterEach, describe, expect, it } from "vitest";
import { befriend, Client, newId, sleep, startInstance, stopInstances, testDb, testRedis, token } from "./helpers.js";

const db = testDb();
afterEach(stopInstances);
afterAll(() => db.end());

async function get(url: string, userId?: string): Promise<{ status: number; body: any }> {
  const headers = userId ? { authorization: `Bearer ${await token(userId)}` } : undefined;
  const response = await fetch(url, { headers });
  return { status: response.status, body: await response.json() };
}

describe("friend messages", () => {
  it("are delivered live across instances, with an ack to the sender", async () => {
    const [one, two] = [await startInstance(), await startInstance()];
    const [alice, bob] = [newId(), newId()];
    befriend(alice, bob);
    const a = await Client.login(one, alice);
    const b = await Client.login(two, bob);

    a.send({ type: "friend_message", to: bob, body: "  gg wp  ", client_id: "c1" });
    const ack = await a.next((m) => m.type === "friend_message_sent");
    expect(ack.client_id).toBe("c1");
    expect(ack.message).toMatchObject({ from_user_id: alice, to_user_id: bob, body: "gg wp" });

    const received = await b.next((m) => m.type === "friend_message");
    expect(received.message).toEqual(ack.message);
    await sleep(100);
    const { rows } = await db.query("SELECT delivered_at FROM chat.messages WHERE id = $1", [ack.message.id]);
    expect(rows[0].delivered_at).not.toBeNull();
  });

  it("wait for an offline friend and are delivered once when they connect", async () => {
    const instance = await startInstance();
    const [alice, bob] = [newId(), newId()];
    befriend(alice, bob);
    const a = await Client.login(instance, alice);
    a.send({ type: "friend_message", to: bob, body: "first" });
    a.send({ type: "friend_message", to: bob, body: "second" });
    await a.next((m) => m.type === "friend_message_sent");
    await a.next((m) => m.type === "friend_message_sent");

    const b = await Client.login(instance, bob);
    expect((await b.next((m) => m.type === "friend_message")).message.body).toBe("first");
    expect((await b.next((m) => m.type === "friend_message")).message.body).toBe("second");
    b.close();
    const again = await Client.login(instance, bob);
    await again.nothing((m) => m.type === "friend_message");
  });

  it("only go to friends, with a length limit and a rate limit", async () => {
    const instance = await startInstance();
    const [alice, bob, stranger] = [newId(), newId(), newId()];
    befriend(alice, bob);
    const a = await Client.login(instance, alice);

    a.send({ type: "friend_message", to: stranger, body: "hi" });
    expect((await a.next((m) => m.type === "error")).error).toBe("not_friends");
    a.send({ type: "friend_message", to: bob, body: "x".repeat(501) });
    expect((await a.next((m) => m.type === "error")).error).toBe("invalid_body");

    for (let i = 0; i < 11; i++) a.send({ type: "friend_message", to: bob, body: `m${i}` });
    for (let i = 0; i < 10; i++) await a.next((m) => m.type === "friend_message_sent");
    expect((await a.next((m) => m.type === "error")).error).toBe("rate_limited");
  });

  it("have a paged history over HTTP, only between the two players", async () => {
    const instance = await startInstance();
    const [alice, bob, carol] = [newId(), newId(), newId()];
    befriend(alice, bob);
    befriend(alice, carol);
    const a = await Client.login(instance, alice);
    for (const [to, body] of [[bob, "1"], [carol, "to carol"], [bob, "2"], [bob, "3"]]) {
      a.send({ type: "friend_message", to, body });
      await a.next((m) => m.type === "friend_message_sent");
    }

    const page1 = await get(`${instance.url}/conversations/${alice}/messages?limit=2`, bob);
    expect(page1.status).toBe(200);
    expect(page1.body.messages.map((m: any) => m.body)).toEqual(["3", "2"]);
    const page2 = await get(`${instance.url}/conversations/${alice}/messages?limit=2&before=${page1.body.next_cursor}`, bob);
    expect(page2.body.messages.map((m: any) => m.body)).toEqual(["1"]);
    expect(page2.body.next_cursor).toBeNull();

    expect((await get(`${instance.url}/conversations/${alice}/messages`)).status).toBe(401);
    expect((await get(`${instance.url}/conversations/${alice}/messages?before=x`, bob)).body.error).toBe("invalid_cursor");
  });

  it("older than the retention period are deleted by the cleanup", async () => {
    const instance = await startInstance();
    const [alice, bob] = [newId(), newId()];
    await db.query(
      "INSERT INTO chat.messages (from_user_id, to_user_id, body, created_at) VALUES ($1, $2, 'old', now() - interval '91 days'), ($1, $2, 'recent', now())",
      [alice, bob],
    );
    // One instance per hour runs the cleanup (lock); release the one taken at startup.
    const redis = testRedis();
    await redis.del("realtime:cleanup:lock");
    await redis.quit();
    await instance.app.cleanup();
    const { rows } = await db.query("SELECT body FROM chat.messages WHERE from_user_id = $1", [alice]);
    expect(rows.map((r) => r.body)).toEqual(["recent"]);
  });
});

describe("GET /presence", () => {
  it("answers only for the caller's friends", async () => {
    const instance = await startInstance();
    const [alice, bob, stranger] = [newId(), newId(), newId()];
    befriend(alice, bob);
    await Client.login(instance, bob);
    await Client.login(instance, stranger);
    const { status, body } = await get(`${instance.url}/presence?ids=${bob},${stranger}`, alice);
    expect(status).toBe(200);
    expect(body.presence).toEqual([{ user_id: bob, status: "online", activity: "menu" }]);
    expect((await get(`${instance.url}/presence?ids=nope`, alice)).body.error).toBe("invalid_ids");
  });
});
