// Throwaway Postgres and Redis for this test run (Docker must be running; nothing to start by hand).
import { PostgreSqlContainer, type StartedPostgreSqlContainer } from "@testcontainers/postgresql";
import { RedisContainer, type StartedRedisContainer } from "@testcontainers/redis";
import type { TestProject } from "vitest/node";
import { migrate } from "../src/migrate.js";

let postgres: StartedPostgreSqlContainer;
let redis: StartedRedisContainer;

export async function setup(project: TestProject) {
  [postgres, redis] = await Promise.all([
    new PostgreSqlContainer("postgres:17").withDatabase("realtime").withUsername("realtime_svc").start(),
    new RedisContainer("redis:7").start(),
  ]);
  await migrate(postgres.getConnectionUri());
  project.provide("databaseUrl", postgres.getConnectionUri());
  project.provide("redisUrl", redis.getConnectionUrl());
}

export async function teardown() {
  await Promise.all([postgres?.stop(), redis?.stop()]);
}

declare module "vitest" {
  export interface ProvidedContext {
    databaseUrl: string;
    redisUrl: string;
  }
}
