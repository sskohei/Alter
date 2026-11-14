"use client";
import { useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, unwrap, type components } from "@alter/api-client";
import { activityApi as browserApi } from "../lib/api-browser";
import { useRetryAfter } from "../lib/use-retry-after";
import { LogoutButton } from "./logout-button";
import { useResource } from "../lib/use-resource";
import { DataState, dataErrorMessage } from "./data-state";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { reauthenticate } from "../app/auth/reauth/actions";
import {
  AlertDialog,
  AlertDialogTrigger,
  AlertDialogContent,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogCancel,
  AlertDialogAction,
} from "./ui/alert-dialog";
type Profile = components["schemas"]["Profile"];
export function AccountSettings({
  initialProfile,
  reauthenticated,
  reauthError,
}: {
  initialProfile: Profile;
  reauthenticated: boolean;
  reauthError: boolean;
}) {
  const router = useRouter();
  const [name, setName] = useState(initialProfile.display_name);
  const [savedName, setSavedName] = useState(initialProfile.display_name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [message, setMessage] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [dialog, setDialog] = useState(false);
  const [needsReauth, setNeedsReauth] = useState(false);
  const retry = useRetryAfter();
  const disabled = busy || retry.seconds > 0;
  function failed(cause: unknown) {
    retry.defer(cause);
    setError(cause);
    if (cause instanceof ApiError && cause.detail.code === "REAUTHENTICATION_REQUIRED") {
      setNeedsReauth(true);
      setConfirmed(false);
    }
  }
  const deletionKey = useRef<string | null>(null);
  const load = useCallback(
    (signal: AbortSignal) =>
      browserApi().GET("/api/v1/preferences", { signal }).then(unwrap),
    [],
  );
  const preferences = useResource(load);
  async function saveName() {
    if (!name.trim() || name.trim().length > 80) {
      setMessage("表示名を1〜80文字で入力してください。");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const profile = await unwrap(
        await browserApi().PATCH("/api/v1/users/me", {
          body: { display_name: name.trim() },
        }),
      );
      setSavedName(profile.display_name);
      setName(profile.display_name);
      setMessage("表示名を保存しました。");
      router.refresh();
    } catch (cause) {
      setMessage("");
      failed(cause);
    } finally {
      setBusy(false);
    }
  }
  async function changePreference(
    field: "web_notifications" | "discord_dm_notifications",
    value: boolean,
  ) {
    setBusy(true);
    setError(null);
    setMessage("通知設定を保存中…");
    try {
      const saved = await unwrap(
        await browserApi().PATCH("/api/v1/preferences", {
          body: { [field]: value },
        }),
      );
      preferences.update(() => saved);
      setMessage("通知設定を保存しました。");
    } catch (cause) {
      setMessage("");
      failed(cause);
    } finally {
      setBusy(false);
    }
  }
  async function deleteAccount() {
    if (!reauthenticated || needsReauth || !confirmed || disabled) return;
    setBusy(true);
    setError(null);
    try {
      deletionKey.current ??= crypto.randomUUID();
      await unwrap(
        await browserApi().POST("/api/v1/users/me/deletion", {
          body: { confirm: true },
          headers: { "Idempotency-Key": deletionKey.current },
        }),
      );
      try {
        for (const key of Object.keys(sessionStorage))
          if (key.startsWith("alter:draft:")) sessionStorage.removeItem(key);
      } catch {}
      // Destroy prior pages and in-memory state after deletion is accepted.
      window.location.replace("/deletion-status");
    } catch (cause) {
      failed(cause);
      setBusy(false);
    }
  }
  return (
    <>
      <h1>アカウント設定</h1>
      {reauthError && (
        <p role="alert" className="notice">
          本人確認を完了できませんでした。同じアカウントで再試行してください。
        </p>
      )}
      {!!error && !dialog && (
        <p role="alert" className="notice">
          {dataErrorMessage(error)}{" "}
          保存済みの表示を確認してから再試行してください。
        </p>
      )}
      {message && <p role="status">{message}</p>}
      {retry.seconds > 0 && <p role="status">あと{retry.seconds}秒後に再試行できます。</p>}
      <section className="card">
        <h2>表示名</h2>
        <p>保存済み: {savedName}</p>
        <form
          className="stack"
          onSubmit={(event) => {
            event.preventDefault();
            void saveName();
          }}
        >
          <label htmlFor="display-name">表示名（80文字まで）</label>
          <Input
            id="display-name"
            value={name}
            maxLength={80}
            required
            onChange={(event) => setName(event.target.value)}
            disabled={disabled}
          />
          <Button disabled={disabled}>表示名を保存</Button>
        </form>
      </section>
      <section className="card">
        <h2>通知</h2>
        <DataState {...preferences} retry={preferences.refresh} />
        {preferences.data && (
          <>
            <label className="check">
              <input
                type="checkbox"
                checked={preferences.data.web_notifications}
                disabled={disabled}
                onChange={(event) =>
                  void changePreference(
                    "web_notifications",
                    event.target.checked,
                  )
                }
              />
              Webで交流のお知らせを受け取る
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={preferences.data.discord_dm_notifications}
                disabled={disabled}
                onChange={(event) =>
                  void changePreference(
                    "discord_dm_notifications",
                    event.target.checked,
                  )
                }
              />
              Discord DMで新着リンクを受け取る（任意）
            </label>
            <p className="help">
              Discord
              DMは初期OFFです。連携済みの本人に新着リンクだけを送ります。会話本文はDMへ送りません。
            </p>
          </>
        )}
      </section>
      <section className="card">
        <h2>ログアウト</h2>
        <LogoutButton />
      </section>
      <section className="card">
        <h2>アカウント削除</h2>
        <p>
          AIを直ちに停止し、関連する共有会話を相手の画面からも非表示にします。関連記憶・通知も削除し、アプリの個人データは原則30日以内に削除します。
        </p>
        <p>
          本文を含まない使用量は本人との紐付けを除いて12か月、匿名化した監査は180日保持します。
        </p>
        <p>
          発行済みの画像URLは最大5分利用できます。Discord投稿や他人が保存したコピーは完全には回収できません。招待時の私的な連絡先で、削除後の確認や問い合わせを受け付けます。
        </p>
        <form action={reauthenticate}>
          <Button variant="outline" disabled={disabled}>
            同じアカウントで本人確認をやり直す
          </Button>
        </form>
        <p className="help">
          {reauthenticated && !needsReauth
            ? "本人確認が完了しました。5分以内に削除を確認してください。"
            : "削除する前に、Google/GitHubで同じ本人の確認が必要です。"}
        </p>
        <AlertDialog
          open={dialog}
          onOpenChange={(open) => {
            if (!busy) {
              setDialog(open);
              setConfirmed(false);
              setError(null);
            }
          }}
        >
          <AlertDialogTrigger asChild>
            <Button variant="outline" disabled={!reauthenticated || needsReauth || disabled}>
              アカウントを削除する
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogTitle className="text-xl font-bold">
              アカウント削除を要求しますか？
            </AlertDialogTitle>
            <AlertDialogDescription>
              AIを停止し、共有会話を相手からも非表示にします。関連記憶・通知も削除し、原則30日以内にパージします。外部の投稿・コピーは完全回収できません。
            </AlertDialogDescription>
            <p>本文を含まない使用量は本人との紐付けを除いて12か月、匿名化した監査は180日保持します。</p>
            <label className="check">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(event) => setConfirmed(event.target.checked)}
                disabled={disabled}
              />
              相手への影響と削除の説明を確認し、アカウント削除を要求します。
            </label>
            {!!error && (
              <p role="alert">
                {dataErrorMessage(error)}{" "}
                本人確認の期限が切れた場合は、キャンセルして本人確認からやり直してください。
              </p>
            )}
            <div className="actions">
              <AlertDialogCancel asChild>
                <Button variant="outline" disabled={disabled}>
                  キャンセル
                </Button>
              </AlertDialogCancel>
              <AlertDialogAction asChild>
                <Button
                  disabled={!confirmed || needsReauth || disabled}
                  onClick={(event) => {
                    event.preventDefault();
                    void deleteAccount();
                  }}
                >
                  {busy ? "受付中…" : "削除を要求する"}
                </Button>
              </AlertDialogAction>
            </div>
          </AlertDialogContent>
        </AlertDialog>
      </section>
    </>
  );
}
