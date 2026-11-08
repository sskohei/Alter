import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, writeFileSync, symlinkSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  acceptanceGate,
  evidenceReader,
  stagingChecks,
} from "./acceptance_gate.mjs";
const commit = "a".repeat(40),
  now = Date.parse("2026-11-08T12:00:00Z");
const data = Buffer.from("synthetic validator fixture, not live acceptance");
const hash = createHash("sha256").update(data).digest("hex");
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
    testers: Array.from({ length: 10 }, (_, i) => ({
      alias: `test${i}`,
      provider: i % 2 ? "google" : "github",
      status: "pass",
    })),
    checks: [...stagingChecks, "production_smoke"].map((id) => ({
      id,
      status: "pass",
      evidence: {
        path: `${id}.txt`,
        sha256: hash,
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
const options = { commit, now, readEvidence: () => data };
test("synthetic fixture checks validator boundaries only", () =>
  assert.deepEqual(acceptanceGate(fixture(), options), []));
for (const [name, mutate] of [
  ["mock provenance", (r) => (r.source = "mock")],
  ["missing commit", (r) => (r.commit = "")],
  ["stale run", (r) => (r.recorded_at = "2020-01-01")],
  ["missing review", (r) => (r.reviewer = "")],
  ["duplicate testers", (r) => (r.testers[1].alias = r.testers[0].alias)],
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
