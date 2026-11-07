import { createMemoryMock, relationshipFixtures } from "./mock-memory";
import { createActivityMock, mockOwnAgentId } from "./mock-activity";
import type { components } from "./preview-schema";

export const scenarios = [
  "normal",
  "empty",
  "401",
  "403",
  "inactive",
  "404",
  "409",
  "422",
  "429",
  "503",
] as const;
export type Scenario = (typeof scenarios)[number];
export const profile: components["schemas"]["Profile"] = {
  id: "00000000-0000-4000-8000-000000000001",
  display_name: "招待ユーザー",
  created_at: "2026-11-07T00:00:00Z",
  web_notifications: true,
  discord_dm_notifications: false,
  onboarded_at: null,
};
export const errorCodes: Record<number, string> = {
  401: "UNAUTHENTICATED",
  403: "INVITATION_REQUIRED",
  404: "NOT_FOUND",
  409: "CONFLICT",
  422: "VALIDATION_ERROR",
  429: "RATE_LIMITED",
  503: "AUTHORIZATION_UNAVAILABLE",
};
export function mockError(status: number, code = errorCodes[status]) {
  return Response.json(
    {
      error: {
        code,
        message: "Mock: 操作を完了できませんでした。",
        request_id: crypto.randomUUID(),
      },
    },
    { status },
  );
}

// Each factory owns its state. Tests/users never share profiles across factories.
export function createMockFetch(
  scenario: Scenario = "normal",
  options: { seedAgent?: boolean; activity?: boolean } = {},
): typeof fetch {
  const activity = createActivityMock(
    scenario === "empty" || !options.activity,
  );
  let agent: components["schemas"]["Agent"] | null = null;
  let revisions: components["schemas"]["ProfileRevision"][] = [];
  let privateInfo = "";
  const blocked = new Set<string>();
  const memory = createMemoryMock(
    scenario === "empty" || !options.activity,
    blocked,
  );
  let current = { ...profile };
  if (options.seedAgent) {
    agent = {
      id: mockOwnAgentId,
      name: "こはる",
      status: "active",
      approved_profile_version: 1,
      created_at: profile.created_at,
      settings: {
        autonomy_enabled: false,
        discord_publish: false,
        frequency_per_day: 1,
        daily_budget_usd: 0.2,
        max_session_usd: 0.02,
        web_visibility: "participants",
        updated_at: profile.created_at,
      },
    };
    revisions = [
      {
        personality: "穏やか",
        values_text: "誠実",
        interests: ["読書"],
        speaking_style: "丁寧",
        interaction_policy: "尊重",
        shareable_info: "好きな本",
        restrictions: "なし",
        version: 1,
        approved_at: profile.created_at,
      },
    ];
  }
  return async (input, init) => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    if (url.pathname === "/health")
      return Response.json({ status: "ok", service: "api", mode: "mock" });
    if (!request.headers.get("Authorization")?.startsWith("Bearer "))
      return mockError(401);
    if (scenario === "inactive") return mockError(403, "ACCOUNT_INACTIVE");
    if (!Number.isNaN(Number(scenario))) return mockError(Number(scenario));
    if (url.pathname === "/api/v1/users/me") {
      if (request.method === "PATCH") {
        const body = await request.json();
        if (
          typeof body.display_name !== "string" ||
          !body.display_name.trim() ||
          body.display_name.length > 200
        )
          return mockError(422);
        current = { ...current, display_name: body.display_name };
      }
      return Response.json(current);
    }
    if (url.pathname === "/api/v1/agents/me" && request.method === "GET")
      return agent ? Response.json(agent) : mockError(404);
    if (url.pathname === "/api/v1/agents" && request.method === "POST") {
      if (agent) return mockError(409);
      const body: components["schemas"]["AgentDraftInput"] =
        await request.json();
      if (!validDraft(body)) return mockError(422);
      agent = {
        id: crypto.randomUUID(),
        name: body.name,
        status: "draft",
        approved_profile_version: null,
        created_at: profile.created_at,
        settings: {
          autonomy_enabled: false,
          discord_publish: false,
          frequency_per_day: 0,
          daily_budget_usd: 0,
          max_session_usd: 0,
          web_visibility: "participants",
          updated_at: profile.created_at,
        },
      };
      privateInfo = body.private_info ?? "";
      revisions = [publicRevision(body, 1)];
      return Response.json(agent, { status: 201 });
    }
    if (agent && url.pathname.startsWith(`/api/v1/agents/${agent.id}`)) {
      const suffix = url.pathname.slice(`/api/v1/agents/${agent.id}`.length);
      if (suffix === "" && request.method === "PATCH") {
        const body = await request.json();
        if (
          typeof body.name !== "string" ||
          !body.name.trim() ||
          body.name.length > 80
        )
          return mockError(422);
        agent = { ...agent, name: body.name };
        return Response.json(agent);
      }
      if (suffix === "/profile" && request.method === "GET")
        return Response.json(revisions.at(-1));
      if (suffix === "/profile/private" && request.method === "GET")
        return Response.json({ private_info: privateInfo });
      if (suffix === "/profile/revisions" && request.method === "POST") {
        const body: components["schemas"]["AgentDraftInput"] =
          await request.json();
        if (!validDraft(body)) return mockError(422);
        const revision = publicRevision(body, revisions.length + 1);
        revisions.push(revision);
        privateInfo = body.private_info ?? "";
        return Response.json(revision);
      }
      if (suffix === "/profile/approve" && request.method === "POST") {
        const { version } = await request.json();
        const latest = revisions.at(-1)!;
        if (latest.version !== version) return mockError(409);
        revisions = revisions.map((r) =>
          r.version === version ? { ...r, approved_at: profile.created_at } : r,
        );
        agent = {
          ...agent,
          status: "active",
          approved_profile_version: version,
        };
        return Response.json(agent);
      }
      if (suffix === "/settings" && request.method === "GET")
        return Response.json(agent.settings);
      if (suffix === "/settings" && request.method === "PATCH") {
        const body = await request.json();
        const keys = Object.keys(body);
        if (
          !keys.length ||
          keys.some(
            (key) =>
              ![
                "autonomy_enabled",
                "frequency_per_day",
                "daily_budget_usd",
                "max_session_usd",
                "discord_publish",
                "web_visibility",
              ].includes(key) || body[key] === null,
          )
        )
          return mockError(422);
        const next = { ...agent.settings!, ...body };
        if (
          typeof next.autonomy_enabled !== "boolean" ||
          typeof next.discord_publish !== "boolean" ||
          !Number.isInteger(next.frequency_per_day) ||
          next.frequency_per_day < 0 ||
          next.frequency_per_day > 2 ||
          ![next.daily_budget_usd, next.max_session_usd].every(
            (n) => typeof n === "number" && Number.isFinite(n) && n >= 0,
          ) ||
          next.web_visibility !== "participants"
        )
          return mockError(422);
        if (next.autonomy_enabled && agent.approved_profile_version === null)
          return mockError(409);
        agent = {
          ...agent,
          settings: { ...next, updated_at: new Date().toISOString() },
        };
        return Response.json(agent.settings);
      }
      const blockMatch = suffix.match(/^\/blocks\/([a-f\d-]{36})$/);
      if (blockMatch && ["PUT", "DELETE"].includes(request.method)) {
        if (
          !relationshipFixtures.some((r) => r.other_agent_id === blockMatch[1])
        )
          return mockError(404);
        if (request.method === "PUT") blocked.add(blockMatch[1]);
        else blocked.delete(blockMatch[1]);
        return new Response(null, { status: 204 });
      }
      // Optional LLM is deliberately unavailable; the manual flow must still work.
      if (suffix === "/profile/suggest" && request.method === "POST")
        return mockError(503, "SUGGESTION_UNAVAILABLE");
    }
    if (url.pathname === "/api/v1/discord/guilds" && request.method === "GET")
      return Response.json(
        options.activity
          ? [
              {
                guild_id: "111111111111111111",
                guild_name: "テスト交流サーバー",
                channel_id: "222222222222222222",
                channel_name: "ai交流",
                guild_enabled: true,
                consented_at: profile.created_at,
                revoked_at: null,
              },
            ]
          : [],
      );
    const memoryResponse = await memory(request);
    if (memoryResponse) return memoryResponse;
    const activityResponse = await activity(request);
    if (activityResponse) return activityResponse;
    return mockError(404);
  };
}

export const pageFixture = (scenario: "normal" | "empty") => ({
  items: scenario === "empty" ? [] : [{ ...profile }],
  next_cursor: null,
});

export function publicRevision(
  body: components["schemas"]["AgentDraftInput"],
  version: number,
): components["schemas"]["ProfileRevision"] {
  // Explicit allowlist: private_info must never enter ordinary responses.
  return {
    personality: body.personality,
    values_text: body.values_text,
    interests: body.interests,
    speaking_style: body.speaking_style,
    interaction_policy: body.interaction_policy,
    shareable_info: body.shareable_info,
    restrictions: body.restrictions,
    hobbies: body.hobbies ?? [],
    strengths: body.strengths ?? [],
    weaknesses: body.weaknesses ?? [],
    version,
    approved_at: null,
  };
}
function validDraft(body: components["schemas"]["AgentDraftInput"]) {
  return (
    typeof body.name === "string" &&
    body.name.trim().length > 0 &&
    body.name.length <= 80 &&
    [
      body.personality,
      body.values_text,
      body.speaking_style,
      body.interaction_policy,
      body.shareable_info,
      body.restrictions,
      body.private_info ?? "",
    ].every((s) => typeof s === "string" && s.length <= 2000) &&
    [
      body.interests,
      body.hobbies ?? [],
      body.strengths ?? [],
      body.weaknesses ?? [],
    ].every(
      (a) =>
        Array.isArray(a) &&
        a.length <= 20 &&
        a.every((s) => typeof s === "string" && s.length <= 2000),
    )
  );
}
