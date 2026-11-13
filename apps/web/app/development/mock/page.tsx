"use client";

import { useState } from "react";
import { ApiError, authenticatedFetch, createApiClient, pagination, unwrap } from "@alter/api-client";
import { createPreviewClient } from "@alter/api-client/preview";
import type { components } from "@alter/api-client/preview";
import { createMockFetch, scenarios, type Scenario } from "@alter/api-client/mock";

type Page = components["schemas"]["ProfilePage"];

export default function MockDevelopment() {
  const [scenario, setScenario] = useState<Scenario>("normal");
  const [name, setName] = useState("招待ユーザー");
  const [status, setStatus] = useState("未確認");
  const [result, setResult] = useState<Page | null>(null);
  const [busy, setBusy] = useState(false);
  const [mock, setMock] = useState(() => createMockFetch());

  async function run(action: "profile" | "save" | "list", cursor?: string | null) {
    setBusy(true);
    setResult(null);
    try {
      const transport = authenticatedFetch({ accessToken: async () => "synthetic", refreshToken: async () => null, onUnauthenticated: () => {}, fetch: mock });
      const client = createApiClient("https://synthetic.invalid", transport);
      if (action === "list") {
        const page = await createPreviewClient("https://synthetic.invalid", transport).GET("/__mock/profiles", { params: { query: pagination(cursor, 2) } }).then(unwrap);
        setResult(page);
        setStatus(page.items.length ? `一覧: ${page.items.length}件` : "一覧は空です");
      } else {
        const profile = await (action === "save" ? client.PATCH("/api/v1/users/me", { body: { display_name: name } }) : client.GET("/api/v1/users/me")).then(unwrap);
        setStatus(`プロフィール: ${profile.display_name}`);
      }
    } catch (error) {
      setStatus(error instanceof ApiError ? `HTTP ${error.status} / ${error.detail.code} / request_id: ${error.detail.request_id}${error.retryAfterSeconds !== undefined ? ` / 再試行まで${error.retryAfterSeconds}秒` : ""}` : "通信に失敗しました。再試行してください。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex max-w-2xl flex-col gap-5 px-6 py-12">
      <h1 className="text-2xl font-bold">API契約・Mock確認</h1>
      <p>合成データ専用です。認証・外部通信・DB更新は行いません。本人確認の検証には使えません。</p>
      <label>応答シナリオ
        <select className="block rounded border p-2" value={scenario} disabled={busy} onChange={e => {
          const next = e.target.value as Scenario;
          setScenario(next); setMock(() => createMockFetch(next)); setResult(null); setStatus("未確認");
        }}>{scenarios.map(s => <option key={s}>{s}</option>)}</select>
      </label>
      <label>表示名<input className="block w-full rounded border p-2" value={name} disabled={busy} onChange={e => setName(e.target.value)} /></label>
      <div className="flex flex-wrap gap-3">
        <button className="rounded border p-2" disabled={busy} onClick={() => run("profile")}>プロフィール取得</button>
        <button className="rounded border p-2" disabled={busy} onClick={() => run("save")}>表示名を保存</button>
        <button className="rounded border p-2" disabled={busy} onClick={() => run("list")}>合成一覧を取得</button>
      </div>
      <p role="status" className="break-all" aria-live="polite">{busy ? "確認中…" : status}</p>
      {result && <><ul>{result.items.map(item => <li key={item.id}>{item.display_name}</li>)}</ul>{result.next_cursor && <button className="rounded border p-2" disabled={busy} onClick={() => run("list", result.next_cursor)}>次のページ</button>}</>}
    </main>
  );
}
