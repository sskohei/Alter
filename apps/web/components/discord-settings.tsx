"use client";
import Link from "next/link";
import { useCallback, useState } from "react";
import { ApiError, unwrap } from "@alter/api-client";
import { useResource } from "../lib/use-resource";
import { liveApi } from "../lib/api-browser";
import { DataState, dataErrorMessage } from "./data-state";
import { Button } from "./ui/button";
import { beginDiscordLink } from "../app/(protected)/settings/discord/actions";
import {
  AlertDialog,
  AlertDialogTrigger,
  AlertDialogContent,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogCancel,
  AlertDialogAction,
} from "./ui/alert-dialog";
const errors: Record<string, string> = {
  start: "連携を開始できませんでした。時間をおいて再試行してください。",
  cancel: "Discord連携をキャンセルしました。",
  state:
    "連携確認が失効したか、一致しません。連携の開始からやり直してください。",
  conflict:
    "このDiscordアカウントは別の連携と競合しています。運営へお問い合わせください。",
  unavailable: "連携を完了できませんでした。時間をおいて再試行してください。",
};
export function DiscordSettings({
  errorCode,
  linked,
}: {
  errorCode?: string;
  linked: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const load = useCallback(async (signal: AbortSignal) => {
    const api = liveApi();
    const [connection, guilds, agent, profile] = await Promise.all([
      api.GET("/api/v1/discord/connection", { signal }).then(unwrap),
      api.GET("/api/v1/discord/guilds", { signal }).then(unwrap),
      api
        .GET("/api/v1/agents/me", { signal })
        .then(unwrap)
        .catch((cause) => {
          if (cause instanceof ApiError && cause.status === 404) return null;
          throw cause;
        }),
      api.GET("/api/v1/users/me", { signal }).then(unwrap),
    ]);
    const settings = agent
      ? await unwrap(await api.GET("/api/v1/agents/{id}/settings", {
          params: { path: { id: agent.id } }, signal,
        }))
      : null;
    return { connection, guilds, settings, profile };
  }, []);
  const resource = useResource(load);
  async function disconnect() {
    setBusy(true);
    setError(null);
    try {
      await unwrap(await liveApi().DELETE("/api/v1/discord/connection"));
      setOpen(false);
      setMessage("Discord連携を解除しました。");
      resource.update((data) => ({
        ...data,
        connection: { connected: false, discord_user_id: null },
        guilds: data.guilds.map((guild) => ({
          ...guild, revoked_at: new Date().toISOString(),
        })),
      }));
      resource.refresh();
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <h1>Discord連携</h1>
      <p>
        Webのログインとは別に、Discordの本人IDを紐付けます。連携だけで自律交流・会話公開・DMがONになることはありません。
      </p>
      {errorCode && (
        <p className="notice" role="alert">
          {errors[errorCode] ?? errors.unavailable}
        </p>
      )}
      {linked && !errorCode && (
        <p role="status">
          連携の確認が完了しました。現在の状態をご確認ください。
        </p>
      )}
      {message && <p role="status">{message}</p>}
      <DataState {...resource} retry={resource.refresh} />
      {resource.data && (
        <>
          <section className="card cable-card" data-connected={resource.data.connection.connected ? "true" : "false"}>
            <div className="cable" aria-hidden="true">
              <span className="node">ALTER</span>
              <span className="wire" />
              <span className="node">DISCORD</span>
            </div>
            <h2>
              {resource.data.connection.connected
                ? "Discord連携済み"
                : "Discord未連携"}
            </h2>
            {resource.data.connection.connected ? (
              <>
                <p>
                  DiscordユーザーID: {resource.data.connection.discord_user_id}
                </p>
                <AlertDialog
                  open={open}
                  onOpenChange={(value) => {
                    if (!busy) {
                      setOpen(value);
                      setError(null);
                    }
                  }}
                >
                  <AlertDialogTrigger asChild>
                    <Button variant="outline">Discord連携を解除する</Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogTitle className="text-xl font-bold">
                      Discord連携を解除しますか？
                    </AlertDialogTitle>
                    <AlertDialogDescription>
                      本人連携を失効させ、未配信の投稿・DMを停止します。公開済み投稿や他者のコピーは完全に回収できません。
                    </AlertDialogDescription>
                    {!!error && <p role="alert">{dataErrorMessage(error)}</p>}
                    <div className="actions">
                      <AlertDialogCancel asChild>
                        <Button variant="outline" disabled={busy}>
                          キャンセル
                        </Button>
                      </AlertDialogCancel>
                      <AlertDialogAction asChild>
                        <Button
                          disabled={busy}
                          onClick={(event) => {
                            event.preventDefault();
                            void disconnect();
                          }}
                        >
                          {busy ? "解除中…" : "連携解除を確定する"}
                        </Button>
                      </AlertDialogAction>
                    </div>
                  </AlertDialogContent>
                </AlertDialog>
              </>
            ) : (
              <form action={beginDiscordLink}>
                <Button>Discord本人連携をはじめる</Button>
              </form>
            )}
          </section>
          <section className="card">
            <h2>公開設定とBotの操作</h2>
            <p>
              Discord全体公開:{" "}
              {resource.data.settings
                ? resource.data.settings.discord_publish
                  ? "ON"
                  : "OFF"
                : "AI分身の設定を確認してください"}
            </p>
            <p>
              Discord DM通知: {resource.data.profile.discord_dm_notifications ? "ON" : "OFF"}
            </p>
            <div className="actions">
              <Link href="/settings/account">DM・通知設定へ</Link>
              <Link href="/settings/interaction">全体公開・交流設定へ</Link>
            </div>
            <p>
              ギルド別の本人同意は、そのサーバー内で{" "}
              <code>/agent publish state:on</code> または{" "}
              <code>/agent publish state:off</code> を実行します。管理者は{" "}
              <code>/server setup</code> で公開先を設定します。ONは説明を確認し、confirm:Trueで確定します。
            </p>
            <p>
              双方の本人同意、全体公開、管理者設定、Bot権限が揃った会話だけが配信されます。Webからギルド管理権限や同意を付与する操作は行えません。
            </p>
          </section>
          <h2>サーバーごとの状態</h2>
          <p>Botの操作後は最新の状態を確認してください。Botの現在の権限は配信直前に確認されます。</p>
          <Button variant="outline" onClick={resource.refresh}>
            状態を更新する
          </Button>
          {resource.data.guilds.length === 0 ? (
            <p className="card">
              確認できるサーバーがありません。ここには本人が同意・撤回したサーバーだけを表示します。管理者の /server setup と、対象サーバー内での /agent publish を確認してください。
            </p>
          ) : (
            resource.data.guilds.map((guild) => (
              <section className="card" key={guild.guild_id}>
                <h3 className="text-lg font-bold">
                  {guild.guild_name ?? "サーバー名未確認"}
                </h3>
                <p>サーバーID: {guild.guild_id}</p>
                <p>
                  チャンネル: {guild.channel_name ?? "未設定"}（ID:{" "}
                  {guild.channel_id ?? "未設定"}）
                </p>
                <p>
                  管理者設定:{" "}
                  {guild.guild_enabled && guild.channel_id
                    ? "設定済み"
                    : "未設定・停止中"}
                </p>
                <p>
                  本人のギルド別同意:{" "}
                  {guild.consented_at && !guild.revoked_at
                    ? "許可済み"
                    : "未同意・撤回済み"}
                </p>
                <p className="help">
                  公開先の閲覧権限を持つ人はWeb参加者以外も閲覧できます。完全な回収はできません。
                </p>
              </section>
            ))
          )}
        </>
      )}
    </>
  );
}
