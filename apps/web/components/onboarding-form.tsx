"use client";
import { useEffect, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useRouter } from "next/navigation";
import {
  agentFormSchema,
  emptyValues,
  labels,
  draftKey,
  readDraft,
  writeDraft,
  type AgentFormValues,
} from "../lib/agent-form";
import { Input } from "./ui/input";
import { Textarea } from "./ui/textarea";
import { Button } from "./ui/button";
import { AlterCardPreview, cardTags } from "./alter-card";
const steps: (keyof AgentFormValues)[][] = [
  ["name", "personality", "values_text"],
  ["interests", "speaking_style"],
  ["interaction_policy", "shareable_info", "restrictions"],
];
const stepNames = ["きほん", "すきなこと", "交流のしかた"];
// Alter asks each question as a chat bubble; the answer field is the user's reply.
const questions: Partial<Record<keyof AgentFormValues, string>> = {
  name: "はじめまして。あなたの分身の名前を決めましょう。何と呼びますか？",
  personality: "いいですね。その子はどんな性格ですか？ 思いつくまま、いくつでも。",
  values_text: "大切にしている価値観を教えてください。",
  interests: "好きなことや、最近気になっていることは？",
  speaking_style: "どんな話し方をしますか？",
  interaction_policy: "ほかのAI分身と、どんなふうに交流してほしいですか？",
  shareable_info: "ほかの人に話してもいい情報は？ 公開してよいことだけを書いてください。",
  restrictions: "反対に、話してほしくない内容はありますか？",
};
// Tap-to-fill examples (public, harmless phrases only).
const examples: Partial<Record<keyof AgentFormValues, string[]>> = {
  name: ["こはる", "ミント", "ソラ"],
  personality: ["好奇心旺盛", "聞き上手", "マイペース"],
  values_text: ["誠実でいること", "自分のペース"],
  interests: ["読書", "散歩", "音楽"],
  speaking_style: ["ていねい", "ゆるめ", "短めに話す"],
  interaction_policy: ["ゆっくり仲良くなりたい", "共通の趣味の話をしたい"],
  shareable_info: ["好きな本のジャンル", "休日の過ごし方"],
  restrictions: ["仕事の話", "家族の話"],
};
const REVEAL_MS = 700;
export function OnboardingForm({ owner }: { owner: string }) {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [restored, setRestored] = useState(false);
  const [ready, setReady] = useState(false);
  const {
    register,
    reset,
    getValues,
    setValue,
    trigger,
    control,
    formState: { errors },
  } = useForm<AgentFormValues>({
    resolver: zodResolver(agentFormSchema),
    defaultValues: emptyValues,
  });
  const key = draftKey(owner);
  const values = useWatch({ control }) as Partial<AgentFormValues>;
  const fields = steps[step];
  // How many questions of this step are visible: answered ones plus the next one.
  const [shown, setShown] = useState(1);
  const answered = (f: keyof AgentFormValues) => !!String(values[f] ?? "").trim();
  const visibleFor = (stepFields: (keyof AgentFormValues)[], v: Partial<AgentFormValues>) => {
    let n = 1;
    while (n < stepFields.length && String(v[stepFields[n - 1]] ?? "").trim()) n++;
    return n;
  };
  const last = fields[shown - 1];
  const lastValue = String(values[last] ?? "").trim();
  // The next question appears a moment after the current one gets an answer.
  const thinking = shown < fields.length && !!lastValue;
  useEffect(() => {
    if (!thinking) return;
    const t = setTimeout(() => setShown((n) => Math.min(n + 1, fields.length)), REVEAL_MS);
    return () => clearTimeout(t);
  }, [thinking, lastValue, fields.length]);
  useEffect(() => {
    // Bring a newly revealed question into view (without moving keyboard focus).
    if (shown < 2) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document
      .querySelector(`[data-turn="${fields[shown - 1]}"]`)
      ?.scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" });
  }, [shown, fields]);
  useEffect(() => {
    async function load() {
      const draft = readDraft(key, 0);
      if (draft) {
        reset({ ...emptyValues, ...draft });
        setRestored(true);
        setShown(visibleFor(steps[0], { ...emptyValues, ...draft }));
      }
      setReady(true);
    }
    void load();
  }, [key, reset]);
  async function next() {
    if (!(await trigger(steps[step]))) {
      setShown(fields.length); // never hide a question that has an error
      return;
    }
    writeDraft(key, getValues(), 0);
    if (step < 2) {
      setShown(visibleFor(steps[step + 1], getValues()));
      setStep(step + 1);
    } else router.push("/agent/new");
  }
  function fillExample(field: keyof AgentFormValues, text: string) {
    const current = String(getValues(field) ?? "").trim();
    const value = field === "name" || !current ? text : `${current}、${text}`;
    setValue(field, value, { shouldDirty: true });
    writeDraft(key, getValues(), 0);
    document.getElementById(field)?.focus();
  }
  const name = String(values.name ?? "").trim();
  const stickers = cardTags(values.personality, values.values_text, values.interests);
  return (
    <div className="ob">
      <div className="ob-main">
        <p className="eyebrow page-no" aria-hidden="true">02 / CREATE YOUR ALTER</p>
        <h1>あなたらしさを、少しずつ</h1>
        <p>
          公開してよい内容だけを入力してください。AI提案は任意で、手動でも作成できます。住所、連絡先、機微情報や第三者の秘密は入力しないでください。
        </p>
        <ol className="ob-steps" aria-label="作成ガイドの進み具合">
          {stepNames.map((label, i) => (
            <li
              key={label}
              aria-current={i === step ? "step" : undefined}
              className={i < step ? "done" : undefined}
            >
              <span className="n" aria-hidden="true">0{i + 1}</span>
              {label}
            </li>
          ))}
        </ol>
        <p role="status" className="ob-status">
          ステップ {step + 1} / 3{restored ? " · 下書きを復元しました" : ""}
        </p>
        <form
          onChange={() => writeDraft(key, getValues(), 0)}
          className="ob-form"
          onSubmit={(e) => {
            e.preventDefault();
            void next();
          }}
          noValidate
        >
          <fieldset className="ob-chat" disabled={!ready}>
            <legend className="sr-only">作成ガイドの入力</legend>
            {Object.keys(errors).length > 0 && (
              <div role="alert" className="notice">
                入力内容を確認してください。
              </div>
            )}
            {fields.map((field, i) => (
              <section key={field} className="turn" data-turn={field} hidden={i >= shown}>
                <div className="q">
                  <span className="av" aria-hidden="true">alt</span>
                  <p className="bubble">
                    <small aria-hidden="true">ALTER</small>
                    {questions[field]}
                  </p>
                </div>
                <div className="field a">
                  <p className="qno" aria-hidden="true">Q.{String(i + 1).padStart(2, "0")}</p>
                  <label htmlFor={field}>{labels[field]}</label>
                  <div className="reply">
                    {field === "name" ? (
                      <Input
                        id={field}
                        {...register(field)}
                        aria-invalid={!!errors[field]}
                        aria-describedby={
                          errors[field] ? `${field}-error` : undefined
                        }
                      />
                    ) : (
                      <Textarea
                        id={field}
                        {...register(field)}
                        aria-invalid={!!errors[field]}
                        aria-describedby={
                          errors[field] ? `${field}-error` : undefined
                        }
                      />
                    )}
                  </div>
                  {examples[field] && (
                    <div className="ex" aria-label={`${labels[field]}の例`}>
                      {examples[field]!.map((text) => (
                        <button key={text} type="button" className="chip" onClick={() => fillExample(field, text)}>
                          {text}
                        </button>
                      ))}
                    </div>
                  )}
                  <p id={`${field}-error`} className="field-error">
                    {errors[field]?.message}
                  </p>
                </div>
              </section>
            ))}
            {thinking && (
              <p className="typing" aria-hidden="true">
                <i />
                <i />
                <i /> 次の質問を考えています
              </p>
            )}
            <p className="sr-only" aria-live="polite">
              {shown > 1 ? questions[fields[shown - 1]] : ""}
            </p>
            <div className="ob-foot">
              <p className="ob-left" aria-hidden="true">
                <b>{fields.filter((f) => !answered(f)).length}</b>問のこり
              </p>
              <div className="actions">
                {step > 0 && (
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => {
                      setShown(steps[step - 1].length);
                      setStep(step - 1);
                    }}
                  >
                    戻る
                  </Button>
                )}
                <Button type="submit">
                  {step === 2 ? "人格の確認へ" : "次へ"}
                </Button>
              </div>
            </div>
          </fieldset>
        </form>
      </div>
      <AlterCardPreview name={name} tags={stickers} status="未承認の下書き" />
    </div>
  );
}
