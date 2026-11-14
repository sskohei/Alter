import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, writeFileSync, symlinkSync, rmSync, readFileSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  acceptanceGate,
  evidenceReader,
  stagingChecks,
} from "./acceptance_gate.mjs";
import { participantOperations, performanceMetrics } from "./acceptance_data.mjs";
import { initializeRun, measureReads } from "./acceptance_run.mjs";
const commit = "a".repeat(40),
  now = Date.parse("2026-11-08T12:00:00Z");
const data = Buffer.from("synthetic validator fixture, not live acceptance");
const hash = createHash("sha256").update(data).digest("hex");
const participants = {
  participants: Array.from({ length: 3 }, (_, i) => ({
    alias: `test${i}`,
    operations: Object.fromEntries(participantOperations.map((op) => [op, "pass"])),
  })),
};
const performance = {
  reads: Array.from({ length: 20 }, () => ({ method: "GET", path: "/api/v1/users/me", status: 200, duration_ms: 1000 })),
  reservations: [{ alias: "conversation-1", status: "completed", reserved_at: "2026-11-08T11:00:00Z", completed_at: "2026-11-08T11:15:00Z" }],
};
const evidenceData = (path) => Buffer.from(JSON.stringify(
  path === "participant_operations.txt" ? participants : performance,
));
function fixture() {
  return {
    commit,
    source: "live",
    environment: "staging",
    recorded_at: new Date(now).toISOString(),
    reviewer: "test-reviewer",
    policy_version: "test-policy",
    release_owner: "test-owner",
    rollback_owner: "test-owner",
    testers: Array.from({ length: 3 }, (_, i) => ({
      alias: `test${i}`,
      provider: i % 2 ? "google" : "github",
      status: "pass",
    })),
    checks: [...stagingChecks, "production_smoke"].map((id) => ({
      id,
      status: "pass",
      evidence: {
        path: `${id}.txt`,
        sha256: ["participant_operations", "performance_measurements"].includes(id)
          ? createHash("sha256").update(evidenceData(`${id}.txt`)).digest("hex") : hash,
        source: "live",
        environment: id === "production_smoke" ? "production" : "staging",
        commit,
        recorded_at: new Date(now).toISOString(),
        reviewer: "test-reviewer",
      },
    })),
    metrics: {
      read_samples: 20,
      read_p95_ms: 1000,
      reservation_samples: 1,
      reservation_max_seconds: 900,
      new_llm_calls_after_stop: 0,
      budget_breaches: 0,
      duplicate_conversations: 0,
      duplicate_posts: 0,
      unknown_auto_resends: 0,
    },
  };
}
const options = { commit, now, readEvidence: (path) =>
  ["participant_operations.txt", "performance_measurements.txt"].includes(path) ? evidenceData(path) : data };
test("synthetic fixture checks validator boundaries only", () =>
  assert.deepEqual(acceptanceGate(fixture(), options), []));
for (const [name, mutate] of [
  ["mock provenance", (r) => (r.source = "mock")],
  ["missing commit", (r) => (r.commit = "")],
  ["stale run", (r) => (r.recorded_at = "2020-01-01")],
  ["missing review", (r) => (r.reviewer = "")],
  ["duplicate testers", (r) => (r.testers[1].alias = r.testers[0].alias)],
  ["only two testers", (r) => r.testers.pop()],
  ["four testers", (r) => r.testers.push({ alias: "extra", provider: "google", status: "pass" })],
  [
    "one provider only",
    (r) => r.testers.forEach((t) => (t.provider = "google")),
  ],
  [
    "pending legal",
    (r) =>
      (r.checks.find(
        (c) => c.id === "legal_retention_provider_pricing",
      ).status = "not_run"),
  ],
  ["mock evidence", (r) => (r.checks[0].evidence.source = "mock")],
  [
    "wrong environment",
    (r) => (r.checks[0].evidence.environment = "production"),
  ],
  [
    "stale evidence commit",
    (r) => (r.checks[0].evidence.commit = "b".repeat(40)),
  ],
  ["tampered digest", (r) => (r.checks[0].evidence.sha256 = "b".repeat(64))],
  ["missing check", (r) => r.checks.pop()],
  ["duplicate check", (r) => r.checks.push(r.checks[0])],
  ["null measurement", (r) => (r.metrics.read_p95_ms = null)],
  ["insufficient samples", (r) => (r.metrics.read_samples = 19)],
  ["slow reservation", (r) => (r.metrics.reservation_max_seconds = 901)],
  ["calls after stop", (r) => (r.metrics.new_llm_calls_after_stop = 1)],
  ["budget breach", (r) => (r.metrics.budget_breaches = 1)],
  ["unknown delivery resend", (r) => (r.metrics.unknown_auto_resends = 1)],
  ["fractional safety counter", (r) => (r.metrics.duplicate_posts = 0.5)],
  ["summary differs from raw readings", (r) => (r.metrics.read_p95_ms = 999)],
])
  test(`reject ${name}`, () => {
    const report = fixture();
    mutate(report);
    assert.ok(acceptanceGate(report, options).length);
  });
test("missing actual artifact fails", () =>
  assert.ok(
    acceptanceGate(fixture(), {
      ...options,
      readEvidence: () => {
        throw new Error();
      },
    }).length,
  ));
test("preproduction does not require production smoke; release does", () => {
  const report = fixture();
  report.checks.pop();
  assert.deepEqual(
    acceptanceGate(report, { ...options, phase: "preproduction" }),
    [],
  );
  assert.ok(acceptanceGate(report, options).length);
  assert.ok(acceptanceGate(report, { ...options, phase: "skip" }).length);
});
test("private evidence paths cannot escape through traversal or symlinks", () => {
  const root = mkdtempSync(join(tmpdir(), "alter-gate-"));
  try {
    writeFileSync(join(root, "evidence.txt"), data);
    symlinkSync("/etc/hosts", join(root, "outside"));
    const reader = evidenceReader(root);
    assert.deepEqual(reader("evidence.txt"), data);
    for (const path of ["/etc/hosts", "../outside", "outside"])
      assert.throws(() => reader(path));
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
test("checked-in template remains NOT READY", async () => {
  const template = JSON.parse(
    await (
      await import("node:fs/promises")
    ).readFile(
      new URL("../docs/acceptance/staging.example.json", import.meta.url),
      "utf8",
    ),
  );
  assert.ok(acceptanceGate(template, options).length);
});

function gateWithEvidence(id, payload) {
  const report = fixture();
  const encoded = Buffer.from(JSON.stringify(payload));
  report.checks.find((c) => c.id === id).evidence.sha256 = createHash("sha256").update(encoded).digest("hex");
  return acceptanceGate(report, { ...options, readEvidence: (path) => path === `${id}.txt` ? encoded : options.readEvidence(path) });
}
for (const op of participantOperations)
  test(`one participant missing ${op} prevents aggregate pass`, () => {
    const matrix = structuredClone(participants);
    matrix.participants[1].operations[op] = "not_run";
    assert.ok(gateWithEvidence("participant_operations", matrix).length);
  });
test("participant evidence must match exact roster", () => {
  const matrix = structuredClone(participants);
  matrix.participants[1].alias = matrix.participants[0].alias;
  assert.ok(gateWithEvidence("participant_operations", matrix).length);
});
test("nearest-rank p95, all reservation durations and failures come from raw data", () => {
  const raw = structuredClone(performance);
  raw.reads.forEach((r, i) => { r.duration_ms = i + 1; });
  assert.equal(performanceMetrics(raw).read_p95_ms, 19);
  raw.reservations.push({ ...raw.reservations[0], alias: "conversation-2", completed_at: "2026-11-08T11:16:00Z" });
  assert.equal(performanceMetrics(raw).reservation_max_seconds, 960);
  assert.ok(gateWithEvidence("performance_measurements", raw).length);
  raw.reads[0].status = 503;
  assert.throws(() => performanceMetrics(raw));
});
test("performance rejects invalid or incomplete records", () => {
  for (const mutate of [
    (r) => { r.reads[0].duration_ms = -1; },
    (r) => { r.reads[0].duration_ms = null; },
    (r) => { r.reads[0].path = "/health"; },
    (r) => { r.reads[0].path = "/api/v1/users/me?token=secret"; },
    (r) => { r.reservations[0].completed_at = null; },
    (r) => { r.reservations[0].completed_at = "2026-11-08T10:59:00Z"; },
    (r) => { r.reservations[0].status = "running"; },
    (r) => { r.reservations.push(r.reservations[0]); },
  ]) {
    const raw = structuredClone(performance);
    mutate(raw);
    assert.throws(() => performanceMetrics(raw));
  }
});
test("initializer creates private unpassed records and never overwrites evidence", () => {
  const root = mkdtempSync(join(tmpdir(), "alter-run-"));
  const run = join(root, "run");
  try {
    const report = initializeRun(run, commit);
    assert.equal(report.testers.length, 3);
    assert.equal(report.commit, commit);
    assert.ok(acceptanceGate(report, options).length);
    assert.equal(statSync(run).mode & 0o777, 0o700);
    assert.equal(statSync(join(run, "report.json")).mode & 0o777, 0o600);
    const matrix = JSON.parse(readFileSync(join(run, "participants.json")));
    assert.equal(matrix.participants.length, 3);
    assert.ok(gateWithEvidence("participant_operations", matrix).length);
    assert.throws(() => initializeRun(run, commit));
    assert.equal(readFileSync(join(run, "report.json"), "utf8"), JSON.stringify(report, null, 2) + "\n");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
test("read collector uses authenticated GETs and saves only sanitized timings, including failures", async () => {
  let calls = 0, time = 0;
  const reads = await measureReads({ api: "https://api.example.test", token: "private-test-token", clock: () => time++, fetcher: async (url, options) => {
    calls++;
    assert.equal(options.redirect, "error");
    assert.equal(options.cache, "no-store");
    assert.equal(options.headers.Authorization, "Bearer private-test-token");
    assert.ok(url.startsWith("https://api.example.test/api/v1/"));
    if (calls === 2) throw new Error("upstream secret must not be printed");
    return new Response("private body", { status: calls === 3 ? 503 : 200 });
  } });
  assert.equal(calls, 20);
  assert.equal(reads[1].status, 0);
  assert.equal(reads[2].status, 503);
  assert.ok(reads.every((r) => r.duration_ms === 1));
  assert.ok(!JSON.stringify(reads).includes("private"));
  assert.throws(() => performanceMetrics({ reads, reservations: performance.reservations }));
});
test("read collector rejects missing credentials and non-HTTPS or non-origin URLs before fetching", async () => {
  for (const api of ["http://api.example.test", "https://api.example.test/path", "https://user:pass@api.example.test", "https://api.example.test?token=secret"]) {
    await assert.rejects(measureReads({ api, token: "secret", fetcher: () => assert.fail("must not fetch") }));
  }
  await assert.rejects(measureReads({ api: "https://api.example.test", token: "" }));
});
