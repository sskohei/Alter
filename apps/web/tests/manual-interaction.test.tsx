import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { ManualInteraction } from "../components/manual-interaction";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("../lib/api-browser", () => ({ activityApi: () => ({ GET: mocks.get, POST: mocks.post }) }));
const ok = (data: unknown) => ({ data, response: new Response(null, { status: 200 }) });

beforeEach(() => {
  vi.clearAllMocks();
  mocks.get.mockResolvedValue(ok({ enabled: true, unlimited_daily_count: true }));
});

test("disabled demo hides the manual button", async () => {
  mocks.get.mockResolvedValue(ok({ enabled: false, unlimited_daily_count: false }));
  render(<ManualInteraction />);
  await waitFor(() => expect(mocks.get).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: "交流を開始する" })).not.toBeInTheDocument();
});

test("start disables duplicate clicks and navigates to the reserved conversation", async () => {
  let resolve!: (value: ReturnType<typeof ok>) => void;
  mocks.post.mockImplementation(() => new Promise((done) => { resolve = done; }));
  render(<ManualInteraction />);
  const button = await screen.findByRole("button", { name: "交流を開始する" });
  fireEvent.click(button);
  expect(screen.getByRole("button", { name: "交流を開始中…" })).toBeDisabled();
  fireEvent.click(button);
  expect(mocks.post).toHaveBeenCalledTimes(1);
  expect(mocks.post.mock.calls[0][1].headers["Idempotency-Key"]).toBeTruthy();
  resolve(ok({ id: "conversation-id" }));
  await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/conversations/conversation-id"));
});

test("lost response reuses its key on retry to prevent a second billed conversation", async () => {
  mocks.post.mockRejectedValueOnce(new TypeError("offline")).mockResolvedValueOnce(ok({ id: "retried" }));
  render(<ManualInteraction />);
  fireEvent.click(await screen.findByRole("button", { name: "交流を開始する" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "交流を開始する" }));
  await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/conversations/retried"));
  expect(mocks.post.mock.calls[0][1].headers["Idempotency-Key"])
    .toBe(mocks.post.mock.calls[1][1].headers["Idempotency-Key"]);
});

test("ineligible partner or budget shows guidance without navigating", async () => {
  mocks.post.mockResolvedValue({
    error: { error: { code: "CONFLICT", message: "No partner", request_id: "synthetic" } },
    response: new Response(null, { status: 409 }),
  });
  render(<ManualInteraction />);
  fireEvent.click(await screen.findByRole("button", { name: "交流を開始する" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("双方の人格承認");
  expect(mocks.push).not.toHaveBeenCalled();
});
