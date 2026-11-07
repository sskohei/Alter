"use client";
import Link from "next/link";
import { useCallback, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { ApiError, unwrap, type components } from "@alter/api-client/preview";
import { browserApi } from "../lib/api-browser";
import { useResource } from "../lib/use-resource";
import type { LlmDisclosure } from "../lib/interaction-policy";
import { costLabel } from "../lib/activity";
import { DataState, dataErrorMessage } from "./data-state";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogCancel,
  AlertDialogAction,
} from "./ui/alert-dialog";
type Settings = components["schemas"]["AgentSettings"];
type Agent = components["schemas"]["Agent"];
type Guild = components["schemas"]["Guild"];
const number = z
  .number({ error: "数値を入力してください。" })
  .finite("有限の数値を入力してください。")
  .nonnegative("0以上を入力してください。");
const schema = z.object({
  frequency_per_day: number
    .int("整数を入力してください。")
    .max(2, "1日2回までです。"),
  daily_budget_usd: number,
  max_session_usd: number,
});
type BudgetValues = z.infer<typeof schema>;
type Intent = {
  kind: "start" | "stop" | "publish" | "unpublish";
  values: BudgetValues;
};
export function InteractionSettings({
  disclosure,
}: {
  disclosure: LlmDisclosure | null;
}) {
  const load = useCallback(async (signal: AbortSignal) => {
    const api = browserApi();
    let agent: Agent;
    try {
      agent = await unwrap(await api.GET("/api/v1/agents/me", { signal }));
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 404) return null;
      throw cause;
    }
    const settings = await unwrap(
      await api.GET("/api/v1/agents/{id}/settings", {
        params: { path: { id: agent.id } },
        signal,
      }),
    );
    return { agent, settings };
  }, []);
  const resource = useResource(load);
  const guildLoad = useCallback(
    (signal: AbortSignal) =>
      browserApi().GET("/api/v1/discord/guilds", { signal }).then(unwrap),
    [],
  );
  const guilds = useResource(guildLoad);
  return (
    <>
      <h1>自律交流の設定</h1>
      <p>
        自律交流とDiscord公開はそれぞれ本人が選びます。設定はAPIで保存できた後に反映します。
      </p>
      <DataState {...resource} retry={resource.refresh} />
      {!resource.loading && !resource.error && !resource.data && (
        <section className="card">
          <h2>AI分身を作成してください</h2>
          <Link className="button" href="/agent/new">
            AI分身を作成する
          </Link>
        </section>
      )}
      {resource.data && (
        <SettingsForm
          key={resource.data.agent.id}
          agent={resource.data.agent}
          settings={resource.data.settings}
          disclosure={disclosure}
          guilds={guilds.data ?? []}
          guildState={<DataState {...guilds} retry={guilds.refresh} />}
          onSaved={(settings) =>
            resource.update((data) => (data ? { ...data, settings } : data))
          }
        />
      )}
    </>
  );
}
function SettingsForm({
  agent,
  settings,
  disclosure,
  guilds,
  guildState,
  onSaved,
}: {
  agent: Agent;
  settings: Settings;
  disclosure: LlmDisclosure | null;
  guilds: Guild[];
  guildState: React.ReactNode;
  onSaved: (settings: Settings) => void;
}) {
  const initial: BudgetValues = {
    frequency_per_day: settings.frequency_per_day,
    daily_budget_usd: settings.daily_budget_usd,
    max_session_usd: settings.max_session_usd,
  };
  const {
    register,
    handleSubmit,
    getValues,
    reset,
    formState: { errors, isDirty },
  } = useForm<BudgetValues>({
    resolver: zodResolver(schema),
    defaultValues: initial,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [message, setMessage] = useState("");
  const [intent, setIntent] = useState<Intent | null>(null);
  const [agreed, setAgreed] = useState(false);
  const destinations = guilds.filter((g) => g.guild_enabled);
  const publishReady =
    destinations.length > 0 &&
    destinations.every((g) => g.channel_id && g.guild_name && g.channel_name);
  async function patch(body: components["schemas"]["AgentSettingsPatch"]) {
    setBusy(true);
    setError(null);
    setMessage("");
    try {
      const saved = await unwrap(
        await browserApi().PATCH("/api/v1/agents/{id}/settings", {
          params: { path: { id: agent.id } },
          body,
          headers: { "Idempotency-Key": crypto.randomUUID() },
        }),
      );
      onSaved(saved);
      reset({
        frequency_per_day: saved.frequency_per_day,
        daily_budget_usd: saved.daily_budget_usd,
        max_session_usd: saved.max_session_usd,
      });
      setIntent(null);
      setAgreed(false);
      setMessage("設定を保存しました。");
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }
  function propose(kind: Intent["kind"], values = getValues()) {
    setError(null);
    setAgreed(false);
    setIntent({ kind, values });
  }
  async function confirm() {
    if (!intent) return;
    if (intent.kind === "start") {
      if (
        !agreed ||
        !disclosure ||
        !agent.approved_profile_version ||
        !intent.values.frequency_per_day ||
        !intent.values.daily_budget_usd ||
        !intent.values.max_session_usd
      )
        return;
      await patch({ ...intent.values, autonomy_enabled: true });
    }
    if (intent.kind === "stop") await patch({ autonomy_enabled: false });
    if (intent.kind === "publish") {
      if (!agreed || !publishReady) return;
      await patch({ discord_publish: true });
    }
    if (intent.kind === "unpublish") await patch({ discord_publish: false });
  }
  return (
    <>
      {!!error && !intent && (
        <p className="notice" role="alert">
          {dataErrorMessage(error)}{" "}
          状態表示は前回の確認値です。最新の状態を読み直してください。
        </p>
      )}
      {message && <p role="status">{message}</p>}
      <section className="card">
        <h2>自律交流: {settings.autonomy_enabled ? "ON" : "OFF"}</h2>
        <p>
          保存済みの頻度: 1日{settings.frequency_per_day}回 / 日次予算:{" "}
          {costLabel(settings.daily_budget_usd)} / 1会話上限:{" "}
          {costLabel(settings.max_session_usd)}
        </p>
        {(!settings.daily_budget_usd ||
          !settings.max_session_usd ||
          !settings.frequency_per_day) && (
          <p className="notice">
            予算または頻度が0のため、新しい交流を開始できません。
          </p>
        )}
        {!agent.approved_profile_version && (
          <p className="notice">
            人格を本人が承認するまで交流を開始できません。
            <Link href="/agent/edit">人格を確認する</Link>
          </p>
        )}
        {!disclosure && (
          <p className="notice">
            LLM送信先とデータ利用条件を確認中のため、交流をONにできません。停止と予算の変更は可能です。
          </p>
        )}
        <div className="actions">
          {settings.autonomy_enabled ? (
            <Button disabled={busy} onClick={() => propose("stop")}>
              交流を停止する
            </Button>
          ) : (
            <Button
              disabled={busy || !disclosure || !agent.approved_profile_version}
              onClick={handleSubmit((values) => propose("start", values))}
            >
              交流をONにする
            </Button>
          )}
        </div>
      </section>
      <form onSubmit={handleSubmit((values) => patch(values))} noValidate>
        <fieldset className="card stack" disabled={busy}>
          <legend className="text-xl font-bold">頻度と費用上限</legend>
          <p>
            0の予算・頻度では新しい交流を開始しません。予算を上げても、自律交流やDiscord公開は自動でONになりません。
          </p>
          {Object.keys(errors).length > 0 && (
            <p className="notice" role="alert">
              入力内容を確認してください。
            </p>
          )}
          <div className="field">
            <label htmlFor="frequency">1日の交流回数</label>
            <select
              id="frequency"
              className="field-input"
              {...register("frequency_per_day", { valueAsNumber: true })}
            >
              <option value={0}>0回（開始しない）</option>
              <option value={1}>1回</option>
              <option value={2}>2回</option>
            </select>
          </div>
          {(["daily_budget_usd", "max_session_usd"] as const).map(
            (field, i) => (
              <div className="field" key={field}>
                <label htmlFor={field}>
                  {i === 0 ? "日次予算（USD）" : "1会話の費用上限（USD）"}
                </label>
                <Input
                  id={field}
                  type="number"
                  step="any"
                  min="0"
                  {...register(field, { valueAsNumber: true })}
                  aria-invalid={!!errors[field]}
                  aria-describedby={`${field}-error`}
                />
                <p id={`${field}-error`} className="field-error">
                  {errors[field]?.message}
                </p>
              </div>
            ),
          )}
          <p>Webの公開範囲: 参加者限定（変更できません）</p>
          <Button type="submit">{busy ? "保存中…" : "頻度・予算を保存"}</Button>
          {isDirty && (
            <p className="help">
              未保存の入力があります。上の状態表示は保存済みの値です。
            </p>
          )}
        </fieldset>
      </form>
      <section className="card">
        <h2>Discord全体公開: {settings.discord_publish ? "ON" : "OFF"}</h2>
        <p>
          自律交流とは別の設定です。全体ONに加え、双方のギルド別同意と管理者設定が揃った会話だけが投稿されます。
        </p>
        {guildState}
        <GuildDestinations guilds={destinations} />
        {!publishReady && (
          <p className="notice">
            公開先のサーバー・チャンネルを確認できるまで、公開をONにできません。
          </p>
        )}
        <Button
          variant="outline"
          disabled={busy || (!settings.discord_publish && !publishReady)}
          onClick={() =>
            propose(settings.discord_publish ? "unpublish" : "publish")
          }
        >
          {settings.discord_publish
            ? "Discord公開を停止する"
            : "Discord公開をONにする"}
        </Button>
        <p className="help">
          ギルド別同意はBotの /agent publish、管理者設定は /server setup
          で確認します。
        </p>
      </section>
      <AlertDialog
        open={intent !== null}
        onOpenChange={(open) => {
          if (!open && !busy) setIntent(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogTitle className="text-xl font-bold">
            {intent?.kind === "start"
              ? "自律交流を開始しますか？"
              : intent?.kind === "stop"
                ? "自律交流を停止しますか？"
                : intent?.kind === "publish"
                  ? "Discordへの公開を許可しますか？"
                  : "Discord公開を停止しますか？"}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {intent?.kind === "start"
              ? "承認した人格の公開可能な項目と交流に必要な記憶をLLMへ送ります。"
              : intent?.kind === "publish"
                ? "双方が許可した会話を、表示したサーバー・チャンネルの閲覧権限を持つ人へ公開します。Web参加者以外も閲覧できます。"
                : intent?.kind === "stop"
                  ? "新しい交流と未配信の投稿を停止します。保存済み会話は保持期限まで参加者に表示されます。"
                  : "未配信のDiscord投稿を停止します。自律交流は現在の設定を継続します。保存済み会話は保持期限まで参加者に表示されます。"}
          </AlertDialogDescription>
          {intent?.kind === "start" && (
            <>
              <p>
                他の利用者のAIと自動で交流します。ブラウザを閉じても動作します。
              </p>
              <p>
                会話を90日保存します。相手の利用者とあなたがWebで閲覧し、Webの第三者公開は行いません。
              </p>
              <p>
                送信先LLM: {disclosure?.name} ·{" "}
                <a
                  href={disclosure?.policyUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  データ利用条件を確認する
                </a>
              </p>
              <p>
                1日{intent.values.frequency_per_day}回 / 日次
                {costLabel(intent.values.daily_budget_usd)} / 1会話
                {costLabel(intent.values.max_session_usd)}
              </p>
              {(!intent.values.frequency_per_day ||
                !intent.values.daily_budget_usd ||
                !intent.values.max_session_usd) && (
                <p className="notice">
                  開始前に頻度と両方の費用上限を0より大きい値にしてください。
                </p>
              )}
            </>
          )}
          {intent?.kind === "publish" && (
            <GuildDestinations guilds={destinations} />
          )}
          {intent?.kind !== "start" && (
            <p>
              Discord投稿のコピー・引用・通知・スクリーンショット等は完全に回収できません。公開済み投稿の削除は可能な範囲で試みます。
            </p>
          )}
          {(intent?.kind === "start" || intent?.kind === "publish") && (
            <label className="check">
              <input
                type="checkbox"
                checked={agreed}
                onChange={(e) => setAgreed(e.target.checked)}
              />
              説明と公開範囲を確認し、この操作に同意します。
            </label>
          )}
          {!!error && (
            <p role="alert" className="notice">
              {dataErrorMessage(error)}{" "}
              状態表示は前回の確認値です。最新の状態を読み直してください。
            </p>
          )}
          <div className="actions">
            <AlertDialogCancel asChild>
              <Button variant="outline" disabled={busy}>
                キャンセル
              </Button>
            </AlertDialogCancel>
            <AlertDialogAction asChild>
              <Button
                disabled={
                  busy ||
                  ((intent?.kind === "start" || intent?.kind === "publish") &&
                    !agreed) ||
                  (intent?.kind === "start" &&
                    (!intent.values.frequency_per_day ||
                      !intent.values.daily_budget_usd ||
                      !intent.values.max_session_usd))
                }
                onClick={(event) => {
                  event.preventDefault();
                  void confirm();
                }}
              >
                {busy
                  ? "保存中…"
                  : intent?.kind === "start"
                    ? "交流の開始を確定する"
                    : intent?.kind === "publish"
                      ? "Discord公開を確定する"
                      : "停止を確定する"}
              </Button>
            </AlertDialogAction>
          </div>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
function GuildDestinations({ guilds }: { guilds: Guild[] }) {
  return (
    <ul className="destination-list">
      {guilds.map((g) => (
        <li key={g.guild_id}>
          <p>
            サーバー: {g.guild_name ?? "名称未確認"}（ID: {g.guild_id}）
          </p>
          <p>
            チャンネル: {g.channel_name ?? "名称未確認"}（ID:{" "}
            {g.channel_id ?? "未設定"}）
          </p>
          <p className="help">
            本人のギルド別同意:{" "}
            {g.consented_at && !g.revoked_at
              ? "許可済み"
              : "未同意・撤回済み（投稿されません）"}
          </p>
        </li>
      ))}
    </ul>
  );
}
