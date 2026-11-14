"use client";

import { useState } from "react";
import { createApiClient } from "@alter/api-client";

export default function Home() {
  const [status, setStatus] = useState("未確認");
  const [loading, setLoading] = useState(false);
  async function checkApi() {
    setLoading(true);
    try {
      const client = createApiClient(process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000");
      const { data, error } = await client.GET("/health");
      setStatus(data ? `API: ${data.status} / ${data.mode}` : `APIエラー: ${JSON.stringify(error)}`);
    } catch {
      setStatus("APIに接続できません。起動とURLを確認してください。");
    } finally {
      setLoading(false);
    }
  }
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-6 px-6 py-12">
      <p className="text-sm font-semibold tracking-widest text-slate-500">ALTER / DEVELOPMENT</p>
      <h1 className="text-4xl font-bold">開発環境の準備ができました</h1>
      <p className="leading-7 text-slate-600">WebとAPIの接続を確認できます。認証とAI分身の機能は、今後の実装で追加します。</p>
      <button onClick={checkApi} disabled={loading} className="w-fit rounded-lg bg-slate-900 px-5 py-3 font-semibold text-white disabled:opacity-50">
        {loading ? "確認中…" : "APIの接続を確認"}
      </button>
      <p role="status" aria-live="polite">{status}</p>
    </main>
  );
}
