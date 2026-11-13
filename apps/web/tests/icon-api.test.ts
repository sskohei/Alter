// @vitest-environment node
import { expect, test, vi, beforeEach } from "vitest";
import { NextRequest } from "next/server";
import { createMockFetch, profile } from "@alter/api-client/mock";
vi.mock("../lib/api-server", () => ({serverApi: async () => ({supabase: {auth: {
  getUser: async () => ({data: {user: {id: "owner"}}, error: null}),
  getSession: async () => ({data: {session: {access_token: "synthetic"}}}),
}}})}));
vi.mock("../lib/origin", () => ({appOrigin: () => "http://localhost:3000"}));
import { POST } from "../app/api/v1/[...path]/route";

beforeEach(() => { vi.unstubAllGlobals(); });
const context = {params: Promise.resolve({path: ["agent-icons"]})};

test("BFF forwards image bytes unchanged with authentication", async () => {
  const data = new Uint8Array([137, 80, 78, 71, 255, 0, 128]);
  const upstream = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    expect([...new Uint8Array(await request.arrayBuffer())]).toEqual([...data]);
    expect(request.headers.get("Content-Type")).toBe("image/png");
    expect(request.headers.get("Authorization")).toBe("Bearer synthetic");
    return Response.json({icon_path: "synthetic"}, {status: 201});
  });
  vi.stubGlobal("fetch", upstream);
  const result = await POST(new NextRequest("http://localhost:3000/api/v1/agent-icons", {
    method: "POST", body: data.buffer,
    headers: {Origin: "http://localhost:3000", "Content-Type": "image/png"},
  }), context);
  expect(result.status).toBe(201);
  expect(upstream).toHaveBeenCalledOnce();
});

test.each(["image/svg+xml", "application/json"])("BFF rejects unsupported type %s", async mime => {
  const upstream = vi.fn(); vi.stubGlobal("fetch", upstream);
  const result = await POST(new NextRequest("http://localhost:3000/api/v1/agent-icons", {
    method: "POST", body: "unsafe", headers: {Origin: "http://localhost:3000", "Content-Type": mime},
  }), context);
  expect(result.status).toBe(422);
  expect(upstream).not.toHaveBeenCalled();
});

test("BFF rejects oversize uploads and cross-origin uploads before forwarding", async () => {
  const upstream = vi.fn(); vi.stubGlobal("fetch", upstream);
  for (const [origin, size, status] of [["http://evil.test", 2, 403], ["http://localhost:3000", 2097153, 422]] as const) {
    const result = await POST(new NextRequest("http://localhost:3000/api/v1/agent-icons", {
      method: "POST", body: new Uint8Array(size).buffer, headers: {Origin: origin, "Content-Type": "image/png"},
    }), context);
    expect(result.status).toBe(status);
  }
  expect(upstream).not.toHaveBeenCalled();
});

test("synthetic Mock accepts an upload, checks its registration, and clears selected icon", async () => {
  const mock = createMockFetch();
  const headers = {Authorization: "Bearer synthetic", "Content-Type": "image/png"};
  const upload = await mock("http://mock.test/api/v1/agent-icons", {method: "POST", headers, body: "synthetic-image"});
  expect(upload.status).toBe(201);
  const {icon_path} = await upload.json();
  expect(icon_path).toMatch(new RegExp(`^${profile.id}/`));
  const draft = {name:"分身", personality:"穏やか", values_text:"対話", interests:["本"], speaking_style:"丁寧", interaction_policy:"尊重", shareable_info:"本", restrictions:"なし", icon_path};
  const jsonHeaders = {Authorization: "Bearer synthetic", "Content-Type": "application/json"};
  const created = await mock("http://mock.test/api/v1/agents", {method: "POST", headers: jsonHeaders, body: JSON.stringify(draft)});
  const agent = await created.json();
  expect(created.status).toBe(201);
  expect(agent.icon_url).toContain("/object/sign/agent-icons/");
  const bad = await mock(`http://mock.test/api/v1/agents/${agent.id}`, {method: "PATCH", headers: jsonHeaders, body: JSON.stringify({icon_path: "foreign/file.png"})});
  expect(bad.status).toBe(422);
  const cleared = await mock(`http://mock.test/api/v1/agents/${agent.id}`, {method: "PATCH", headers: jsonHeaders, body: JSON.stringify({icon_path: null})});
  expect((await cleared.json()).icon_url).toBeNull();
});
