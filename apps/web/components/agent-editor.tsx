"use client";
import { useEffect, useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { AlterCardPreview, cardTags } from "./alter-card";
import { zodResolver } from "@hookform/resolvers/zod";
import { useRouter } from "next/navigation";
import { unwrap } from "@alter/api-client/preview";
import { browserApi, operationMessage } from "../lib/api-browser";
import {
  agentFormSchema,
  emptyValues,
  labels,
  publicFields,
  toDraft,
  toForm,
  draftKey,
  readDraft,
  writeDraft,
  removeDraft,
  type Agent,
  type Revision,
  type Personality,
  type AgentFormValues,
} from "../lib/agent-form";
import { AgentPreview } from "./agent-preview";
import { Input } from "./ui/input";
import { Textarea } from "./ui/textarea";
import { Button } from "./ui/button";
import {
  AlertDialog,
  AlertDialogTrigger,
  AlertDialogContent,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogCancel,
  AlertDialogAction,
} from "./ui/alert-dialog";

export function AgentEditor({
  owner,
  initial,
}: {
  owner: string;
  initial?: { agent: Agent; revision: Revision };
}) {
  const router = useRouter();
  const defaults = initial
    ? toForm(initial.agent, initial.revision)
    : emptyValues;
  const {
    register,
    handleSubmit,
    reset,
    setValue,
    getValues,
    control,
    formState: { errors, isDirty },
  } = useForm<AgentFormValues>({
    resolver: zodResolver(agentFormSchema),
    defaultValues: defaults,
  });
  const live = useWatch({ control }) as Partial<AgentFormValues>;
  const [agent, setAgent] = useState(initial?.agent);
  const [saved, setSaved] = useState(
    initial?.revision.approved_at === null ? initial.revision : null,
  );
  const [preview, setPreview] = useState<AgentFormValues | null>(
    initial?.revision.approved_at === null ? defaults : null,
  );
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [restored, setRestored] = useState(false);
  const [privateReady, setPrivateReady] = useState(!initial);
  const [ready, setReady] = useState(false);
  const [suggestion, setSuggestion] = useState<Personality | null>(null);
  const [expectedVersion, setExpectedVersion] = useState(initial?.revision.version);
  const leaving = useRef(false);
  const approvalKeys = useRef(new Map<number, string>());
  const key = draftKey(owner, initial?.agent.id);
  const baseVersion = initial?.revision.version ?? 0;
  const api = browserApi;
  useEffect(() => {
    async function load() {
      const draft = readDraft(key, baseVersion);
      if (draft) {
        reset({ ...defaults, ...draft }, { keepDefaultValues: true });
        setRestored(true);
        setSaved(null);
        setPreview(null);
      }
      setReady(true);
    }
    void load();
    // defaults are the immutable server snapshot for this editor mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, baseVersion, reset]);
  async function loadPrivate() {
    if (!initial) return;
    try {
      const data = await unwrap(
        await api().GET("/api/v1/agents/{id}/profile/private", {
          params: { path: { id: initial.agent.id } },
        }),
      );
      setValue("private_info", data.private_info, { shouldDirty: false });
      setPrivateReady(true);
    } catch (cause) {
      setMessage(operationMessage(cause));
    }
  }
  useEffect(() => {
    const id = initial?.agent.id;
    if (!id) return;
    let active = true;
    void browserApi()
      .GET("/api/v1/agents/{id}/profile/private", { params: { path: { id } } })
      .then(unwrap)
      .then((data) => {
        if (active) {
          setValue("private_info", data.private_info, { shouldDirty: false });
          setPrivateReady(true);
        }
      })
      .catch((cause) => {
        if (active) setMessage(operationMessage(cause));
      });
    return () => {
      active = false;
    };
  }, [initial?.agent.id, setValue]);
  useEffect(() => {
    if (!isDirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (!leaving.current) event.preventDefault();
    };
    const click = (event: MouseEvent) => {
      const link = (event.target as Element).closest?.(
        "a[href]",
      ) as HTMLAnchorElement | null;
      if (
        !link ||
        link.origin !== location.origin ||
        link.pathname === location.pathname ||
        leaving.current
      )
        return;
      if (!window.confirm("未保存の変更があります。画面を離れますか？")) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", click, true);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", click, true);
    };
  }, [isDirty]);

  async function save(values: AgentFormValues) {
    setBusy(true);
    setMessage("");
    setAgreed(false);
    try {
      const body = toDraft(values);
      let current = agent;
      let revision: Revision;
      if (!current) {
        current = await unwrap(
          await api().POST("/api/v1/agents", {
            body,
            headers: { "Idempotency-Key": crypto.randomUUID() },
          }),
        );
        setAgent(current);
        revision = await unwrap(
          await api().GET("/api/v1/agents/{id}/profile", {
            params: { path: { id: current.id } },
          }),
        );
      } else {
        if (expectedVersion === undefined) {
          setMessage("最新の人格を読み直してから保存してください。入力内容は保持しています。");
          return;
        }
        if (current.name !== values.name) {
          current = await unwrap(
            await api().PATCH("/api/v1/agents/{id}", {
              params: { path: { id: current.id } },
              body: { name: values.name },
            }),
          );
          setAgent(current);
        }
        revision = await unwrap(
          await api().POST("/api/v1/agents/{id}/profile/revisions", {
            params: { path: { id: current.id } },
            body: { ...body, expected_version: expectedVersion },
          }),
        );
      }
      setExpectedVersion(revision.version);
      setSaved(revision);
      setPreview({ ...values, private_info: "" });
      reset(values);
      removeDraft(key);
      setMessage(
        "下書きを保存しました。公開可能な項目と差分を確認し、この版を承認してください。",
      );
    } catch (cause) {
      setMessage(operationMessage(cause));
    } finally {
      setBusy(false);
    }
  }
  async function approve() {
    if (!agent || !saved || !agreed || isDirty) return;
    setBusy(true);
    setMessage("");
    try {
      if (!approvalKeys.current.has(saved.version))
        approvalKeys.current.set(saved.version, crypto.randomUUID());
      await unwrap(
        await api().POST("/api/v1/agents/{id}/profile/approve", {
          params: { path: { id: agent.id } },
          body: { version: saved.version },
          headers: {
            "Idempotency-Key": approvalKeys.current.get(saved.version)!,
          },
        }),
      );
      leaving.current = true;
      removeDraft(key);
      removeDraft(draftKey(owner));
      router.push("/agent");
      router.refresh();
    } catch (cause) {
      setMessage(operationMessage(cause));
      setBusy(false);
    }
  }
  async function suggest() {
    if (!agent) return;
    setBusy(true);
    setMessage("");
    try {
      const values = getValues();
      const answers = Object.fromEntries(
        publicFields.map((field) => [field, values[field]]),
      );
      setSuggestion(
        await unwrap(
          await api().POST("/api/v1/agents/{id}/profile/suggest", {
            params: { path: { id: agent.id } },
            body: { answers },
          }),
        ),
      );
    } catch (cause) {
      setMessage(operationMessage(cause));
    } finally {
      setBusy(false);
    }
  }
  function applySuggestion() {
    if (!suggestion || !agent) return;
    reset(
      { ...toForm(agent, suggestion), private_info: getValues().private_info },
      { keepDefaultValues: true },
    );
    setSaved(null);
    setPreview(null);
    setAgreed(false);
    setSuggestion(null);
  }
  return (
    <>
      <p className="eyebrow page-no">
        02 / STUDIO · AI分身 /{" "}
        {initial ? `人格の編集 · 版 ${initial.revision.version}` : "人格の作成"}
      </p>
      <h1>{initial ? "あなたらしさを更新する" : "あなたのAI分身をつくる"}</h1>
      <p>
        本人が確認・承認した人格だけを使用します。人格を承認しても、自律交流・Discord公開は始まりません。
      </p>
      <p className="notice">
        公開可能な項目は、交流を有効にした後にLLMへ送信されます。住所、連絡先、機微情報、第三者の秘密を入力しないでください。
      </p>
      {restored && (
        <p role="status">
          公開可能な項目の下書きを復元しました。非公開欄は復元対象外です。
        </p>
      )}
      {message && (
        <div role="alert" className="notice" tabIndex={-1}>
          {message}
        </div>
      )}
      {initial && !privateReady && (
        <Button variant="outline" onClick={loadPrivate}>
          非公開欄の読込を再試行
        </Button>
      )}
      <div className="studio">
      <form
        className="studio-form"
        onSubmit={handleSubmit(save)}
        onChange={(event) => {
          const target = event.target;
          if (
            (target instanceof HTMLInputElement ||
              target instanceof HTMLTextAreaElement) &&
            target.name !== "private_info"
          )
            writeDraft(key, getValues(), baseVersion);
          setAgreed(false);
        }}
        noValidate
      >
        {Object.keys(errors).length > 0 && (
          <div role="alert" className="notice">
            <p>入力内容を確認してください。</p>
            <ul>
              {Object.entries(errors).map(([field, error]) => (
                <li key={field}>
                  <a href={`#${field}`}>
                    {labels[field as keyof AgentFormValues]}: {error?.message}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        )}
        <fieldset
          disabled={busy || !ready || !privateReady}
          className="card stack"
        >
          <legend className="sr-only">人格の入力</legend>
          {(Object.keys(labels) as (keyof AgentFormValues)[]).map((field) => (
            <div key={field} className="field">
              <label htmlFor={field}>{labels[field]}</label>
              {field === "name" ? (
                <Input
                  id={field}
                  {...register(field)}
                  aria-invalid={!!errors[field]}
                  aria-describedby={`${field}-help ${field}-error`}
                />
              ) : (
                <Textarea
                  id={field}
                  {...register(field)}
                  aria-invalid={!!errors[field]}
                  aria-describedby={`${field}-help ${field}-error`}
                  autoComplete="off"
                />
              )}
              <p id={`${field}-help`} className="help">
                {field === "private_info"
                  ? "本人の編集時だけ参照します。LLM・他者・Discordへは送りません。空のままでかまいません。"
                  : [
                        "interests",
                        "hobbies",
                        "strengths",
                        "weaknesses",
                      ].includes(field)
                    ? "読点・カンマ・改行で区切って20項目まで。"
                    : field === "name"
                      ? "80文字以内。"
                      : "公開してよい内容を2000文字以内で。禁止事項がなければ「なし」と入力できます。"}
              </p>
              <p id={`${field}-error`} className="field-error">
                {errors[field]?.message}
              </p>
            </div>
          ))}
          <div className="actions">
            <Button type="submit">
              {busy ? "保存中…" : "下書きを保存して確認"}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                if (
                  !isDirty ||
                  window.confirm(
                    "未保存の変更を破棄して、最新の人格を読み直しますか？",
                  )
                ) {
                  leaving.current = true;
                  removeDraft(key);
                  window.location.reload();
                }
              }}
            >
              最新の人格を読み直す
            </Button>
          </div>
        </fieldset>
      </form>
      <AlterCardPreview
        name={String(live.name ?? "").trim()}
        tags={cardTags(live.personality, live.values_text, live.interests)}
        status={saved ? `版 ${saved.version} · 未承認` : agent ? "編集中" : "未保存の下書き"}
        version={saved ? `v${saved.version}` : "v0"}
      />
      </div>
      {agent && (
        <section className="card">
          <h2>AIからの提案（任意）</h2>
          <p>
            公開可能な項目だけを使います。失敗しても手動入力で続けられます。提案は本人の確認前に承認しません。
          </p>
          <Button variant="outline" disabled={busy} onClick={suggest}>
            AI提案を試す
          </Button>
          {suggestion && (
            <>
              <AgentPreview values={toForm(agent, suggestion)} />
              <Button onClick={applySuggestion}>この提案を編集に使う</Button>
            </>
          )}
        </section>
      )}
      {saved && preview && (
        <section className="card approve-card" aria-label="人格の確認" data-agreed={agreed ? "true" : "false"}>
          <span className="approve-stamp" aria-hidden="true">APPROVED</span>
          <h2>版 {saved.version} の確認・本人承認</h2>
          <p>
            未承認の下書きです。非公開欄の内容はプレビュー・差分には表示しません。
          </p>
          <AgentPreview
            values={preview}
            previous={initial ? defaults : undefined}
          />
          {isDirty && (
            <p className="notice">
              未保存の変更があります。もう一度下書きを保存してから承認してください。
            </p>
          )}
          <label className="check">
            <input
              type="checkbox"
              checked={agreed}
              disabled={busy || isDirty}
              onChange={(e) => setAgreed(e.target.checked)}
            />
            この人格の公開可能な項目を確認し、この版をAI分身の生成に使うことを承認します。
          </label>
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button disabled={!agreed || busy || isDirty}>
                この版を承認する
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogTitle className="text-xl font-bold">
                版 {saved.version} を承認しますか？
              </AlertDialogTitle>
              <AlertDialogDescription>
                この人格をAI分身に使用することを承認します。自律交流・Discord公開は別の設定で、ここでは開始しません。
              </AlertDialogDescription>
              <div className="actions">
                <AlertDialogCancel asChild>
                  <Button variant="outline" disabled={busy}>
                    戻る
                  </Button>
                </AlertDialogCancel>
                <AlertDialogAction asChild>
                  <Button
                    disabled={busy}
                    onClick={(e) => {
                      e.preventDefault();
                      void approve();
                    }}
                  >
                    {busy ? "承認中…" : "承認して保存"}
                  </Button>
                </AlertDialogAction>
              </div>
              {message && <p role="alert">{message}</p>}
            </AlertDialogContent>
          </AlertDialog>
        </section>
      )}
    </>
  );
}
