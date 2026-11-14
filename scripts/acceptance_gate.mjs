import { readFileSync, realpathSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve } from "node:path";
import { createHash } from "node:crypto";
import { pathToFileURL } from "node:url";
import { participantCount, performanceMetrics, validateParticipants } from "./acceptance_data.mjs";

export const stagingChecks = [
  "oauth_invitation",
  "participant_operations",
  "autonomous_browser_closed",
  "dual_consent_discord",
  "ownership_direct_api",
  "independent_process_recovery",
  "duplicate_cron_worker",
  "unknown_delivery_hold",
  "stop_and_budget",
  "deletion_and_external_retention",
  "mobile_axe_keyboard_screen_reader",
  "legal_retention_provider_pricing",
  "invitation_cli",
  "invitation_rls_limit_identity",
  "invitation_fail_closed_atomic_revoke",
  "purge_reoauth_restore",
  "performance_measurements",
  "web_security_callbacks",
  "monitoring_rollback_handoff",
];
const sha = /^[a-f0-9]{40}$/;
const digest = /^[a-f0-9]{64}$/;
const nonempty = (value) =>
  typeof value === "string" && value.trim().length > 0;
export function acceptanceGate(
  report,
  { commit, phase = "release", now = Date.now(), readEvidence } = {},
) {
  const errors = [];
  if (!["preproduction", "release"].includes(phase)) return ["invalid phase"];
  if (!report || typeof report !== "object" || Array.isArray(report))
    return ["report object required"];
  const add = (condition, message) => {
    if (!condition) errors.push(message);
  };
  const recent = (value) => {
    const time = typeof value === "string" ? Date.parse(value) : NaN;
    return (
      Number.isFinite(time) && time <= now + 60000 && time >= now - 7 * 86400000
    );
  };
  add(
    sha.test(commit ?? "") && report.commit === commit,
    "current commit mismatch",
  );
  add(
    report.source === "live" && report.environment === "staging",
    "live staging report required",
  );
  add(
    recent(report.recorded_at) && nonempty(report.reviewer),
    "recent dated reviewer required",
  );
  add(
    nonempty(report.policy_version) &&
      nonempty(report.rollback_owner) &&
      nonempty(report.release_owner),
    "policy version and operations owners required",
  );
  const testers = Array.isArray(report.testers) ? report.testers : [];
  add(
    testers.length === participantCount &&
      testers.every(
        (t) =>
          nonempty(t?.alias) &&
          t.status === "pass" &&
          ["google", "github"].includes(t.provider),
      ) &&
      new Set(testers.map((t) => t?.alias)).size === participantCount &&
      new Set(testers.map((t) => t?.provider)).size === 2,
    "three distinct passed testers and both OAuth providers required",
  );
  const checks = Array.isArray(report.checks) ? report.checks : [];
  const required = [
    ...stagingChecks,
    ...(phase === "release" ? ["production_smoke"] : []),
  ];
  for (const id of required) {
    const matches = checks.filter((check) => check?.id === id);
    if (matches.length !== 1 || matches[0].status !== "pass") {
      errors.push(`${id}: passed check required`);
      continue;
    }
    const evidence = matches[0].evidence;
    if (
      !evidence ||
      evidence.source !== "live" ||
      evidence.environment !==
        (id === "production_smoke" ? "production" : "staging") ||
      evidence.commit !== commit ||
      !recent(evidence.recorded_at) ||
      !nonempty(evidence.reviewer) ||
      !nonempty(evidence.path) ||
      !digest.test(evidence.sha256 ?? "")
    ) {
      errors.push(`${id}: live dated evidence required`);
      continue;
    }
    try {
      if (typeof readEvidence !== "function")
        throw new Error("Evidence reader missing");
      const data = readEvidence(evidence.path);
      if (
        !data.length ||
        createHash("sha256").update(data).digest("hex") !== evidence.sha256
      )
        throw new Error("Invalid evidence");
      if (id === "participant_operations")
        validateParticipants(JSON.parse(data.toString()), testers);
      if (id === "performance_measurements") {
        const measured = performanceMetrics(JSON.parse(data.toString()));
        for (const [key, value] of Object.entries(measured)) {
          if (report.metrics?.[key] !== value)
            throw new Error("Reported metrics differ from raw measurements");
        }
      }
    } catch {
      errors.push(`${id}: evidence missing, invalid, hash or measurement mismatch`);
    }
  }
  const metrics = report.metrics ?? {};
  for (const [key, minimum, maximum] of [
    ["read_samples", 20, Infinity],
    ["read_p95_ms", 0, 1000],
    ["reservation_samples", 1, Infinity],
    ["reservation_max_seconds", 0, 900],
    ["new_llm_calls_after_stop", 0, 0],
    ["budget_breaches", 0, 0],
    ["duplicate_conversations", 0, 0],
    ["duplicate_posts", 0, 0],
    ["unknown_auto_resends", 0, 0],
  ])
    add(
      typeof metrics[key] === "number" &&
        Number.isFinite(metrics[key]) &&
        metrics[key] >= minimum &&
        metrics[key] <= maximum &&
        (!["read_p95_ms", "reservation_max_seconds"].includes(key) ? Number.isInteger(metrics[key]) : true),
      `metric ${key}: missing or outside limit`,
    );
  return errors;
}
export function evidenceReader(root) {
  const base = realpathSync(root);
  return (path) => {
    if (!nonempty(path) || isAbsolute(path))
      throw new Error("Relative evidence path required");
    const file = realpathSync(resolve(base, path));
    const local = relative(base, file);
    if (local.startsWith("..") || isAbsolute(local))
      throw new Error("Evidence escapes private report directory");
    return readFileSync(file);
  };
}
if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  try {
    const args = process.argv.slice(2);
    const value = (name) => args[args.indexOf(name) + 1];
    if (!args.includes("--report") || !args.includes("--commit"))
      throw new Error("Missing arguments");
    const file = resolve(value("--report"));
    const errors = acceptanceGate(JSON.parse(readFileSync(file, "utf8")), {
      commit: value("--commit"),
      phase: args.includes("--phase") ? value("--phase") : "release",
      readEvidence: evidenceReader(dirname(file)),
    });
    if (errors.length) {
      console.error("NOT READY");
      errors.forEach((error) => console.error(error));
      process.exitCode = 1;
    } else
      console.log(
        "Evidence completeness passed; release owner must review evidence before publishing.",
      );
  } catch {
    console.error(
      "NOT READY: valid --report <private JSON> --commit <current SHA> [--phase preproduction|release] required",
    );
    process.exitCode = 1;
  }
}
