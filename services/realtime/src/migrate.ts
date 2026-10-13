// Apply migrations/*.sql in name order, each once, recorded in chat.schema_migrations.
// Run with `node dist/migrate.js` (the container does it before starting).
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import pg from "pg";
import { loadConfig } from "./config.js";

export async function migrate(databaseUrl: string): Promise<string[]> {
  const dir = join(dirname(fileURLToPath(import.meta.url)), "..", "migrations");
  const client = new pg.Client({ connectionString: databaseUrl });
  await client.connect();
  const applied: string[] = [];
  try {
    await client.query("CREATE SCHEMA IF NOT EXISTS chat");
    await client.query("CREATE TABLE IF NOT EXISTS chat.schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())");
    const done = new Set((await client.query("SELECT name FROM chat.schema_migrations")).rows.map((r) => r.name));
    for (const name of readdirSync(dir).filter((f) => f.endsWith(".sql")).sort()) {
      if (done.has(name)) continue;
      await client.query("BEGIN");
      try {
        await client.query(readFileSync(join(dir, name), "utf8"));
        await client.query("INSERT INTO chat.schema_migrations (name) VALUES ($1)", [name]);
        await client.query("COMMIT");
        applied.push(name);
      } catch (error) {
        await client.query("ROLLBACK");
        throw error;
      }
    }
  } finally {
    await client.end();
  }
  return applied;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const applied = await migrate(loadConfig().databaseUrl);
  console.log(applied.length ? `Applied ${applied.join(", ")}` : "Database up to date");
}
