import { createActivityMock, mockOwnAgentId } from "./mock-activity";
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import schema from "../openapi.json";
import type { components } from "./schema";
import type { components as Preview } from "./preview-schema";

const ajv = new Ajv2020({ strict: false });
addFormats(ajv);
ajv.addSchema(schema, "live");
const validPatch = ajv.compile({ $ref: "live#/components/schemas/ProfilePatch" });
const validSettingsPatch = ajv.compile({ $ref: "live#/components/schemas/AgentSettingsPatch" });

export const scenarios = ["normal", "empty", "401", "403", "inactive", "404", "409", "422", "429", "503"] as const;
export type Scenario = typeof scenarios[number];
export const profile: components["schemas"]["Profile"] = {
  id: "00000000-0000-4000-8000-000000000001", display_name: "招待ユーザー",
  created_at: "2026-11-07T00:00:00Z", web_notifications: true,
  discord_dm_notifications: false, onboarded_at: null,
};
export const errorCodes: Record<number, components["schemas"]["ErrorBody"]["code"]> = {401: "UNAUTHENTICATED", 403: "INVITATION_REQUIRED", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 409: "CONFLICT", 422: "VALIDATION_ERROR", 429: "RATE_LIMITED", 503: "AUTHORIZATION_UNAVAILABLE"};
export function mockError(status: number, code = errorCodes[status]) {
  const requestId = crypto.randomUUID();
  return Response.json({ error: { code, message: "Mock: 操作を完了できませんでした。", request_id: requestId } } satisfies components["schemas"]["ErrorResponse"], {
    status, headers: { "X-Request-ID": requestId, "Cache-Control": "no-store", ...(status === 429 ? { "Retry-After": "60" } : {}) },
  });
}
function success(body: unknown) {
  return Response.json(body, { headers: { "X-Request-ID": crypto.randomUUID(), "Cache-Control": "no-store" } });
}

// Each factory owns its state. Tests/users never share profiles across factories.
export function createMockFetch(
  scenario: Scenario = "normal",
  options: { seedAgent?: boolean; activity?: boolean } = {},
): typeof fetch {
  const activity = createActivityMock(
    scenario === "empty" || !options.activity,
  );
  let agent: Preview["schemas"]["Agent"] | null = null;
  let revisions: Preview["schemas"]["ProfileRevision"][] = [];
  let privateInfo = "";
  const blocked = new Set<string>();
  const icons = new Set<string>();
  const iconUrl = (path: string | null | undefined) => path ? `https://mock.invalid/storage/v1/object/sign/agent-icons/${path}?token=synthetic` : null;
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
    if (url.pathname === "/health") return request.method === "GET" ? success({ status: "ok", service: "api", mode: "mock" }) : mockError(405);
    if (!/^\/api\/v1\/(?:conversations(?:\/[a-f\d-]{36})?|notifications(?:\/[a-f\d-]{36}\/read)?|discord\/(?:connection|guilds))$/.test(url.pathname) && !["/api/v1/users/me", "/api/v1/agent-icons", "/__mock/profiles"].includes(url.pathname) && !/^\/api\/v1\/agents(?:\/me|\/[a-f\d-]{36}(?:\/blocks\/[a-f\d-]{36}|\/settings|\/profile(?:\/private|\/revisions|\/approve|\/suggest)?)?)?$/.test(url.pathname)) return mockError(404);
    if (["/api/v1/users/me", "/__mock/profiles"].includes(url.pathname) && !(url.pathname === "/api/v1/users/me" ? ["GET", "PATCH"] : ["GET"]).includes(request.method)) return mockError(405);
    if (!/^Bearer \S+$/i.test(request.headers.get("Authorization") ?? "")) return mockError(401);
    if (scenario === "inactive") return mockError(403, "ACCOUNT_INACTIVE");
    if (!Number.isNaN(Number(scenario))) return mockError(Number(scenario));
    if (url.pathname === "/api/v1/agent-icons") {
      if (request.method !== "POST") return mockError(405);
      const bytes = new Uint8Array(await request.arrayBuffer());
      if (!["image/png", "image/jpeg", "image/webp"].includes(request.headers.get("Content-Type") ?? "") || !bytes.length || bytes.length > 2097152) return mockError(422);
      // Synthetic transport only; real image decoding and metadata stripping are tested in Python.
      const path = `${profile.id}/${crypto.randomUUID()}.png`;
      icons.add(path);
      return Response.json({ icon_path: path }, { status: 201 });
    }
    if (url.pathname === "/__mock/profiles") {
      const limitText = url.searchParams.get("limit") ?? "20";
      const limit = Number(limitText);
      const cursor = url.searchParams.get("cursor");
      const items = pageFixture(scenario as "normal" | "empty").items;
      const offset = cursor === null ? 0 : items.findIndex(item => `after:${item.id}` === cursor) + 1;
      if (!/^\d+$/.test(limitText) || !Number.isInteger(limit) || limit < 1 || limit > 50 || (cursor !== null && offset === 0)) return mockError(422);
      const page = items.slice(offset, offset + limit);
      return success({ items: page, next_cursor: offset + limit < items.length ? `after:${page.at(-1)!.id}` : null } satisfies Preview["schemas"]["ProfilePage"]);
    }
    if (url.pathname === "/api/v1/users/me") {
      if (request.method === "PATCH") {
        let body: unknown;
        try { body = await request.json(); } catch { return mockError(422); }
        // Pydantic normalizes before applying its generated length constraints.
        if (typeof body === "object" && body !== null && "display_name" in body && typeof body.display_name === "string") {
          const normalized = body.display_name.trim();
          if (/[\u0000-\u001f\u007f-\u009f]/u.test(normalized)) return mockError(422);
          body.display_name = normalized;
        }
        if (!validPatch(body)) return mockError(422);
        current = { ...current, display_name: (body as components["schemas"]["ProfilePatch"]).display_name };
      }
      return success(current);
    }
    if (url.pathname === "/api/v1/agents/me" && request.method === "GET")
      return agent ? Response.json(agent) : mockError(404);
    if (url.pathname === "/api/v1/agents" && request.method === "POST") {
      if (agent) return mockError(409);
      const body: Preview["schemas"]["AgentDraftInput"] =
        await request.json();
      if (!validDraft(body) || (body.icon_path != null && !icons.has(body.icon_path))) return mockError(422);
      agent = {
        id: crypto.randomUUID(),
        name: body.name,
        status: "draft",
        icon_url: iconUrl(body.icon_path),
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
        if ((!Object.hasOwn(body, "name") && !Object.hasOwn(body, "icon_path")) ||
            (Object.hasOwn(body, "name") && (typeof body.name !== "string" || !body.name.trim() || body.name.length > 80)) ||
            (body.icon_path != null && !icons.has(body.icon_path))) return mockError(422);
        agent = { ...agent, ...(Object.hasOwn(body, "name") ? {name: body.name} : {}),
                  ...(Object.hasOwn(body, "icon_path") ? {icon_url: iconUrl(body.icon_path)} : {}) };
        return Response.json(agent);
      }
      if (suffix === "/profile" && request.method === "GET") {
        const version = url.searchParams.get("version");
        if (version !== null && (!/^\d+$/.test(version) || Number(version) < 1)) return mockError(422);
        const revision = version === null ? revisions.at(-1) : revisions.find(r => r.version === Number(version));
        return revision ? Response.json(revision) : mockError(404);
      }
      if (suffix === "/profile/private" && request.method === "GET")
        return Response.json({ private_info: privateInfo });
      if (suffix === "/profile/revisions" && request.method === "POST") {
        const body: Preview["schemas"]["RevisionInput"] =
          await request.json();
        if (!validDraft(body)) return mockError(422);
        if (!Number.isInteger(body.expected_version) || body.expected_version < 1) return mockError(422);
        if (body.expected_version !== revisions.at(-1)!.version) return mockError(409);
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
          r.version === version ? { ...r, approved_at: r.approved_at ?? profile.created_at } : r,
        );
        current = { ...current, onboarded_at: current.onboarded_at ?? profile.created_at };
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
        const parsed: unknown = await request.json();
        if (!validSettingsPatch(parsed) || Object.values(parsed as object).some(value => value === null)) return mockError(422);
        const body = parsed as Partial<components["schemas"]["AgentSettings"]>;
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
        const turningOn = (next.autonomy_enabled && !agent.settings?.autonomy_enabled) ||
          (next.discord_publish && !agent.settings?.discord_publish);
        if (turningOn && (agent.status !== "active" || !agent.approved_profile_version))
          return mockError(409);
        if (("daily_budget_usd" in body || "max_session_usd" in body || turningOn) &&
            (next.max_session_usd > next.daily_budget_usd || next.daily_budget_usd > 1))
          return mockError(422);
        if ([next.daily_budget_usd, next.max_session_usd].some(value => Number(value.toFixed(6)) !== value))
          return mockError(422);
        agent = {
          ...agent,
          settings: { ...next, updated_at: new Date().toISOString() },
        };
        return Response.json(agent.settings);
      }
      const blockMatch = suffix.match(/^\/blocks\/([a-f\d-]{36})$/);
      if (blockMatch && ["PUT", "DELETE"].includes(request.method)) {
        if (blockMatch[1] !== "20000000-0000-4000-8000-000000000001")
          return mockError(404);
        if (request.method === "PUT") blocked.add(blockMatch[1]);
        else blocked.delete(blockMatch[1]);
        return new Response(null, { status: 204 });
      }
      // Optional LLM is deliberately unavailable; the manual flow must still work.
      if (suffix === "/profile/suggest" && request.method === "POST")
        return mockError(503, "SERVICE_UNAVAILABLE");
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
    const activityResponse = await activity(request);
    if (activityResponse) return activityResponse;
    return mockError(404);
  };
}

// This is a mock-only collection, never a production user directory.
export const pageFixture = (scenario: "normal" | "empty"): Preview["schemas"]["ProfilePage"] => ({
  items: scenario === "empty" ? [] : Array.from({ length: 3 }, (_, i) => ({ ...profile, id: `00000000-0000-4000-8000-${String(i + 1).padStart(12, "0")}`, display_name: `合成ユーザー${i + 1}` })),
  next_cursor: null,
});

export function publicRevision(
  body: Preview["schemas"]["AgentDraftInput"],
  version: number,
): Preview["schemas"]["ProfileRevision"] {
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
function validDraft(body: Preview["schemas"]["AgentDraftInput"]) {
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
