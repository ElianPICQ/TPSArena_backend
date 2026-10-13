// Copy the event contract from a released tpsarena-common: npm run sync:events -- 0.2.0
// Reads libs/common/schemas/events.json at git tag common-v<version> (run inside the repo).
// Like the Python services pinning a tag, the service only changes contract when you run this.
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const version = process.argv[2];
if (!/^\d+\.\d+\.\d+$/.test(version ?? "")) {
  console.error("Usage: npm run sync:events -- X.Y.Z");
  process.exit(1);
}
const tag = `common-v${version}`;
const here = dirname(fileURLToPath(import.meta.url));
const contracts = join(here, "..", "contracts");

execFileSync("git", ["fetch", "--quiet", "--tags"], { stdio: "inherit" });
const content = execFileSync("git", ["show", `${tag}:libs/common/schemas/events.json`], { encoding: "utf8" });
mkdirSync(contracts, { recursive: true });
writeFileSync(join(contracts, "events.json"), content);
writeFileSync(join(contracts, "VERSION"), `${version}\n`);
console.log(`contracts/events.json now from ${tag}. Run npm run build (or test) to regenerate the types.`);
