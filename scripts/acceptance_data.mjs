// Structured evidence shared by the initializer, gate and measurement tooling.
export const participantCount = 3;
export const participantOperations = [
  "login", "create", "approve", "enable", "disable", "budget", "block",
  "history", "notifications", "delete_memory", "unlink_discord", "delete_account",
];

export function validateParticipants(data, testers) {
  if (!Array.isArray(data?.participants) || data.participants.length !== participantCount)
    throw new Error("Three participant operation records required");
  const aliases = new Set(testers.map((t) => t.alias));
  for (const row of data.participants) {
    if (!aliases.delete(row?.alias) || !participantOperations.every((op) => row.operations?.[op] === "pass"))
      throw new Error("Every participant must pass every operation");
  }
  if (aliases.size) throw new Error("Participant aliases differ from report");
}

// Nearest-rank p95; failed requests remain in the population and fail acceptance.
export function performanceMetrics(data) {
  const reads = data?.reads;
  const reservations = data?.reservations;
  if (!Array.isArray(reads) || reads.length < 20 || !Array.isArray(reservations) || !reservations.length)
    throw new Error("At least 20 reads and one reservation required");
  const finite = (n) => typeof n === "number" && Number.isFinite(n) && n >= 0;
  const routes = new Set([
    "/api/v1/users/me", "/api/v1/agents/me", "/api/v1/agents/{id}/profile",
    "/api/v1/agents/{id}/settings", "/api/v1/conversations", "/api/v1/conversations/{id}",
    "/api/v1/notifications", "/api/v1/memories", "/api/v1/relationships",
    "/api/v1/preferences", "/api/v1/discord/connection", "/api/v1/discord/guilds",
  ]);
  for (const row of reads) {
    if (row?.method !== "GET" || !routes.has(row.path) || row.status !== 200 || !finite(row.duration_ms))
      throw new Error("Successful ordinary API reads with sanitized route templates required");
  }
  const aliases = new Set();
  const durations = reservations.map((row) => {
    const start = typeof row?.reserved_at === "string" ? Date.parse(row.reserved_at) : NaN;
    const end = typeof row?.completed_at === "string" ? Date.parse(row.completed_at) : NaN;
    if (!/^conversation-[1-9][0-9]*$/.test(row?.alias ?? "") || aliases.has(row.alias) ||
        row.status !== "completed" || !Number.isFinite(start) || !Number.isFinite(end) || end < start)
      throw new Error("Unique completed reservation records required, including slow or failed runs");
    aliases.add(row.alias);
    return (end - start) / 1000;
  });
  const sorted = reads.map((r) => r.duration_ms).sort((a, b) => a - b);
  return {
    read_samples: reads.length,
    read_p95_ms: sorted[Math.ceil(sorted.length * 0.95) - 1],
    reservation_samples: reservations.length,
    reservation_max_seconds: Math.max(...durations),
  };
}
