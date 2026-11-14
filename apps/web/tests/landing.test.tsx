import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import Home from "../app/page";

// next/image needs the Next.js image pipeline; a plain <img> is enough for markup checks.
vi.mock("next/image", () => ({
  // eslint-disable-next-line @next/next/no-img-element
  default: ({ alt, className }: { alt: string; className?: string }) => <img alt={alt} className={className} />,
}));

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.motion;
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addEventListener: () => {}, removeEventListener: () => {} }));
});

test("トップは1つの見出しと、ログインへの導線を持つ", () => {
  render(<Home />);
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("もうひとりの自分が、友だちをつくってくる。");
  const cta = screen.getByRole("link", { name: /招待を受けた方はこちら/ });
  expect(cta).toHaveAttribute("href", "/login");
  // The e2e auth flow selects this link by name, so it must stay unique.
  expect(screen.getAllByRole("link", { name: /招待を受けた方はこちら/ })).toHaveLength(1);
  // Logo appears in the header and the hero, each announced as "Alter".
  expect(screen.getAllByRole("img", { name: "Alter" })).toHaveLength(2);
  expect(screen.getByRole("link", { name: /^Alter\s?ホーム$/ })).toHaveAttribute("href", "/");
});

test("AIは本人ではないこと、初期OFFであること、招待制であることを示す", () => {
  render(<Home />);
  const main = screen.getByRole("main");
  expect(main).toHaveTextContent("本人の発言・意思を表すものではありません");
  expect(within(main).getAllByText("初期 OFF")).toHaveLength(3);
  expect(main).toHaveTextContent("18歳以上・招待制");
  for (const link of screen.getAllByRole("link", { name: "利用規約案・プライバシー説明" })) expect(link).toHaveAttribute("href", "/legal");
});

test("装飾（3D・HUD・流れる帯）は読み上げ対象から外れている", () => {
  const { container } = render(<Home />);
  for (const selector of [".lp-stage", ".lp-ticker", ".lp-rail"]) {
    expect(container.querySelector(selector)).toHaveAttribute("aria-hidden", "true");
  }
  container.querySelectorAll("img").forEach((img) => expect(img).toHaveAttribute("alt", ""));
});

test("動きを止めるボタンで、アニメーションを止めて設定を覚える", () => {
  render(<Home />);
  const toggle = screen.getByRole("button", { name: "動きを止める" });
  expect(toggle).toHaveAttribute("aria-pressed", "false");
  expect(document.documentElement.dataset.motion).toBe("on");
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-pressed", "true");
  expect(document.documentElement.dataset.motion).toBe("off");
  expect(localStorage.getItem("alter-motion")).toBe("off");
});

test("OSで動きを減らす設定なら、最初から止まっている", () => {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: true, media: query, addEventListener: () => {}, removeEventListener: () => {} }));
  render(<Home />);
  expect(screen.getByRole("button", { name: "動きを止める" })).toHaveAttribute("aria-pressed", "true");
  expect(document.documentElement.dataset.motion).toBe("off");
});
