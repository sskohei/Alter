import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import Home from "../app/page";

afterEach(() => vi.unstubAllGlobals());

test("API接続結果を表示し、確認中は二重送信を防ぐ", async () => {
  let resolve!: (response: Response) => void;
  const fetchMock = vi.fn(() => new Promise<Response>((done) => { resolve = done; }));
  vi.stubGlobal("fetch", fetchMock);
  render(<Home />);

  fireEvent.click(screen.getByRole("button", { name: "APIの接続を確認" }));
  expect(screen.getByRole("button", { name: "確認中…" })).toBeDisabled();
  resolve(Response.json({ status: "ok", service: "api", mode: "mock" }));

  expect(await screen.findByText("API: ok / mock")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button")).toBeEnabled();
});

test("通信失敗時に復旧方法を表示して再試行できる", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
  render(<Home />);
  fireEvent.click(screen.getByRole("button", { name: "APIの接続を確認" }));

  expect(await screen.findByText(
    "APIに接続できません。起動とURLを確認してください。",
  )).toBeInTheDocument();
  expect(screen.getByRole("button")).toBeEnabled();
});
