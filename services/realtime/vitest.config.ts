import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    globalSetup: ["tests/global-setup.ts"],
    testTimeout: 20_000,
    hookTimeout: 120_000,
    fileParallelism: false, // the instances share the same streams and consumer group
  },
});
