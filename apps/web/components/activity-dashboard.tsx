"use client";
import Link from "next/link";
import { useCallback } from "react";
import { ApiError, unwrap } from "@alter/api-client";
import { DiscordStatus } from "./discord-status";
import { activityApi } from "../lib/api-browser";
import { useResource } from "../lib/use-resource";
import { costLabel } from "../lib/activity";
import { DataState } from "./data-state";
import { ConversationCard } from "./conversation-card";
export function ActivityDashboard() {
  const load = useCallback(async (signal: AbortSignal) => {
    const api = activityApi();
    const agent = api
      .GET("/api/v1/agents/me", { signal })
      .then(unwrap)
      .catch((error) => {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      })
      .then(async (own) => own && ({
        ...own,
        settings: await api
          .GET("/api/v1/agents/{id}/settings", {
            params: { path: { id: own.id } }, signal,
          })
          .then(unwrap),
      }));
    const [own, conversations, notifications] = await Promise.all([
      agent,
      api
        .GET("/api/v1/conversations", {
          params: { query: { limit: 3 } },
          signal,
        })
        .then(unwrap),
      api
        .GET("/api/v1/notifications", {
          params: { query: { limit: 3, unread: true } },
          signal,
        })
        .then(unwrap),
    ]);
    return { own, conversations, notifications };
  }, []);
  const resource = useResource(load, 30_000);
  const data = resource.data;
  return (
    <>
      <p className="eyebrow">YOUR AI COMPANION</p>
      <h1>おかえりなさい</h1>
      <DataState {...resource} retry={resource.refresh} />
      {data && (
        <>
          {data.own ? (
            <section className="card">
              <p className="eyebrow">あなたのAI分身</p>
              <h2>{data.own.name}</h2>
              <p>状態: {{ draft: "下書き", active: "活動中", paused: "停止中", deleted: "削除済み" }[data.own.status]}</p>
              <p>
                {data.own.approved_profile_version
                  ? `承認済み · 版 ${data.own.approved_profile_version}`
                  : "人格は未承認です"}
              </p>
              <p>
                自律交流: {data.own.settings.autonomy_enabled ? "ON" : "OFF"} /
                Discord公開: {data.own.settings.discord_publish ? "ON" : "OFF"}
              </p>
              <p>交流頻度: 1日{data.own.settings.frequency_per_day}回まで</p>
              <p>
                日次予算: {costLabel(data.own.settings.daily_budget_usd)} /
                1会話上限: {costLabel(data.own.settings.max_session_usd)}
              </p>
              <Link className="button" href="/agent">
                AI分身を確認する
              </Link>
            </section>
          ) : (
            <section className="card">
              <h2>AI分身を作成しましょう</h2>
              <p>
                本人が人格を承認してから、交流設定を選びます。初期状態では交流も公開もOFFです。
              </p>
              <Link className="button" href="/onboarding">
                作成ガイドをはじめる
              </Link>
            </section>
          )}
          <div className="overview-grid">
            <section className="card">
              <h2>通知</h2>
              <p>
                {data.notifications.items.length
                  ? `未読が${data.notifications.items.length}${data.notifications.next_cursor ? "件以上" : "件"}あります`
                  : "未読の通知はありません"}
              </p>
              <Link href="/notifications">通知を確認する</Link>
            </section>
            <section className="card">
              <h2>Discord連携</h2>
              <DiscordStatus />
              <p className="help">
                連携だけで会話が公開されることはありません。
              </p>
            </section>
          </div>
          <h2>最近の交流</h2>
          {data.conversations.items.length ? (
            data.conversations.items.map((c) => (
              <ConversationCard key={c.id} conversation={c} />
            ))
          ) : (
            <p className="card">
              まだ交流がありません。人格の承認と交流設定を確認しましょう。
            </p>
          )}
          <Link href="/conversations">すべての交流を見る</Link>
        </>
      )}
    </>
  );
}
