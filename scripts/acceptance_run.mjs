import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve, join } from "node:path";
import { execFileSync } from "node:child_process";
import { performance } from "node:perf_hooks";
import { pathToFileURL } from "node:url";
import { participantCount, participantOperations, performanceMetrics } from "./acceptance_data.mjs";

export async function measureReads({ api, token, fetcher = fetch, clock = () => performance.now() }) {
  const url = new URL(api);
  if (url.protocol !== "https:" || url.origin !== api || url.username || url.password || !token?.trim())
    throw new Error("Exact HTTPS API origin and participant access token required");
  const reads = [];
  // Ordinary authenticated reads only. Never follow redirects with the credential.
  for (let i = 0; i < 20; i++) {
    const path = ["/api/v1/users/me", "/api/v1/agents/me", "/api/v1/conversations", "/api/v1/notifications"][i % 4];
    const start = clock();
    let status = 0;
    try {
      const response = await fetcher(`${api}${path}`, {
        headers: { Authorization: `Bearer ${token}` }, redirect: "error", cache: "no-store",
        signal: AbortSignal.timeout(15000),
      });
      await response.arrayBuffer();
      status = response.status;
    } catch {
      // Preserve failed requests as status 0; do not expose upstream error text.
    }
    reads.push({ method: "GET", path, status, duration_ms: clock() - start });
  }
  return reads;
}

export function initializeRun(directory, commit) {
  if (!/^[a-f0-9]{40}$/.test(commit)) throw new Error("Full source commit required");
  const report = JSON.parse(readFileSync(new URL("../docs/acceptance/staging.example.json", import.meta.url)));
  report.commit = commit;
  const matrix = {
    participants: Array.from({ length: participantCount }, (_, i) => ({
      alias: `tester-${i + 1}`,
      operations: Object.fromEntries(participantOperations.map((op) => [op, "not_run"])),
    })),
  };
  // A new directory is mandatory: never overwrite earlier evidence.
  mkdirSync(directory, { mode: 0o700 });
  for (const [name, data] of Object.entries({
    "report.json": report,
    "participants.json": matrix,
    "performance.json": { reads: [], reservations: [] },
  }))
    writeFileSync(join(directory, name), JSON.stringify(data, null, 2) + "\n", { mode: 0o600, flag: "wx" });
  return report;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const [command, file, ...extra] = process.argv.slice(2);
    if (!file || extra.length) throw new Error("Invalid arguments");
    if (command === "init") {
      const commit = execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();
      initializeRun(resolve(file), commit);
      console.log("Private run initialized; every case remains NOT READY. See docs/26-mvp-acceptance.md.");
    } else if (command === "metrics") {
      console.log(JSON.stringify(performanceMetrics(JSON.parse(readFileSync(resolve(file), "utf8"))), null, 2));
    } else if (command === "measure") {
      if (existsSync(resolve(file))) throw new Error("Output already exists");
      const reads = await measureReads({ api: process.env.ALTER_ACCEPTANCE_API_ORIGIN, token: process.env.ALTER_ACCEPTANCE_TOKEN });
      writeFileSync(resolve(file), JSON.stringify({ reads, reservations: [] }, null, 2) + "\n", { mode: 0o600, flag: "wx" });
      console.log("Read measurements saved without response bodies. Add all reservation results before computing metrics.");
      if (reads.some((r) => r.status !== 200)) process.exitCode = 1;
    } else throw new Error("Invalid command");
  } catch {
    console.error("NOT READY: use acceptance:run init <new private directory>, measure <new measurements.json>, or metrics <performance.json>; check private inputs and existing output files.");
    process.exitCode = 1;
  }
}
