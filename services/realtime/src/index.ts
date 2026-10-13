import { createApp } from "./app.js";
import { loadConfig } from "./config.js";

const app = createApp(loadConfig());
await app.start();

for (const signal of ["SIGTERM", "SIGINT"] as const) {
  process.once(signal, () => {
    void app.stop().then(() => process.exit(0));
  });
}
