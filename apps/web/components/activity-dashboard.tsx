"use client";
import Image from "next/image";
import Link from "next/link";
import pair from "../public/visuals/pair.png";
import { useCallback } from "react";
import { ApiError, unwrap } from "@alter/api-client/preview";
import { browserApi } from "../lib/api-browser";
import { useResource } from "../lib/use-resource";
import { costLabel } from "../lib/activity";
import { DataState } from "./data-state";
import { ConversationCard } from "./conversation-card";
export function ActivityDashboard() {
  const load = useCallback(async (signal: AbortSignal) => {
    const api = browserApi();
    const agent = api
      .GET("/api/v1/agents/me", { signal })
      .then(unwrap)
      .catch((error) => {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      });
    const [own, conversations, notifications, discord] = await Promise.all([
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
      api.GET("/api/v1/discord/connection", { signal }).then(unwrap),
    ]);
    return { own, conversations, notifications, discord };
  }, []);
  const resource = useResource(load, 30_000);
  const data = resource.data;
  const unread = data?.notifications.items.length ?? 0;
  return (
    <>
      <section className="home-hero">
        <div>
          <p className="eyebrow">YOUR AI COMPANION — 01 / HOME</p>
          <h1>おかえりなさい</h1>
          {data?.own && (
            <p className="home-state">
              <b>{data.own.name}</b>は
              {data.own.settings?.autonomy_enabled
                ? "自律交流がONになっています"
                : "待機中です"}
            </p>
          )}
        </div>
        {/* Decorative: the same 3D pair as the landing page, floating; it squishes on hover. */}
        <div className="float-stage" aria-hidden="true">
          <div className="float">
            <Image src={pair} alt="" className="float-obj" sizes="(max-width: 767px) 60vw, 320px" priority />
          </div>
          <span className="float-shadow" />
        </div>
      </section>
      <DataState {...resource} retry={resource.refresh} />
      {data && (
        <>
          <div className="bento">
            {data.own ? (
              <section className="card tile tile-agent">
                <p className="eyebrow">あなたのAI分身</p>
                <h2>{data.own.name}</h2>
                <p>
                  {data.own.approved_profile_version
                    ? `承認済み · 版 ${data.own.approved_profile_version}`
                    : "人格は未承認です"}
                </p>
                <p>
                  自律交流: {data.own.settings?.autonomy_enabled ? "ON" : "OFF"} /
                  Discord公開: {data.own.settings?.discord_publish ? "ON" : "OFF"}
                </p>
                <p>
                  日次予算: {costLabel(data.own.settings?.daily_budget_usd)} /
                  1会話上限: {costLabel(data.own.settings?.max_session_usd)}
                </p>
                <Link className="button" href="/agent">
                  AI分身を確認する
                </Link>
              </section>
            ) : (
              <section className="card tile tile-agent">
                <h2>AI分身を作成しましょう</h2>
                <p>
                  本人が人格を承認してから、交流設定を選びます。初期状態では交流も公開もOFFです。
                </p>
                <Link className="button" href="/onboarding">
                  作成ガイドをはじめる
                </Link>
              </section>
            )}
            <section className="card tile tile-note">
              <p className="tile-k" aria-hidden="true">NOTIFICATIONS</p>
              <p className="tile-big" aria-hidden="true">
                {unread}
                {data.notifications.next_cursor ? "+" : ""}
              </p>
              <h2>通知</h2>
              <p>
                {unread
                  ? `未読が${unread}${data.notifications.next_cursor ? "件以上" : "件"}あります`
                  : "未読の通知はありません"}
              </p>
              <Link className="more" href="/notifications">
                通知を確認する
              </Link>
            </section>
            <section className="card tile tile-discord">
              <p className="tile-k" aria-hidden="true">DISCORD</p>
              <span className="tile-tag" aria-hidden="true">
                {data.discord.connected ? "ON" : "OFF"}
              </span>
              <h2>Discord連携</h2>
              <p className="tile-big-text">
                {data.discord.connected ? "連携済み" : "未連携"}
              </p>
              <p className="help">
                連携だけで会話が公開されることはありません。
              </p>
            </section>
          </div>
          <div className="sec-head">
            <span className="sec-no" aria-hidden="true">02</span>
            <h2>最近の交流</h2>
            <Link className="more" href="/conversations">
              すべての交流を見る
            </Link>
          </div>
          {data.conversations.items.length ? (
            <div className="conv-strip">
              {data.conversations.items.map((c) => (
                <ConversationCard key={c.id} conversation={c} />
              ))}
            </div>
          ) : (
            <p className="card">
              まだ交流がありません。人格の承認と交流設定を確認しましょう。
            </p>
          )}
        </>
      )}
    </>
  );
}
