"use client";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
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
const steps: (keyof AgentFormValues)[][] = [
  ["name", "personality", "values_text"],
  ["interests", "speaking_style"],
  ["interaction_policy", "shareable_info", "restrictions"],
];
export function OnboardingForm({ owner }: { owner: string }) {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [restored, setRestored] = useState(false);
  const [ready, setReady] = useState(false);
  const {
    register,
    reset,
    getValues,
    trigger,
    formState: { errors },
  } = useForm<AgentFormValues>({
    resolver: zodResolver(agentFormSchema),
    defaultValues: emptyValues,
  });
  const key = draftKey(owner);
  useEffect(() => {
    async function load() {
      const draft = readDraft(key, 0);
      if (draft) {
        reset({ ...emptyValues, ...draft });
        setRestored(true);
      }
      setReady(true);
    }
    void load();
  }, [key, reset]);
  async function next() {
    if (!(await trigger(steps[step]))) return;
    writeDraft(key, getValues(), 0);
    if (step < 2) setStep(step + 1);
    else router.push("/agent/new");
  }
  return (
    <div>
      <h1>あなたらしさを、少しずつ</h1>
      <p>
        公開してよい内容だけを入力してください。AI提案は任意で、手動でも作成できます。住所、連絡先、機微情報や第三者の秘密は入力しないでください。
      </p>
      <p role="status">
        ステップ {step + 1} / 3{restored ? " · 下書きを復元しました" : ""}
      </p>
      <progress value={step + 1} max={3} aria-label="作成ガイドの進捗" />
      <form
        onChange={() => writeDraft(key, getValues(), 0)}
        className="card stack"
        onSubmit={(e) => {
          e.preventDefault();
          void next();
        }}
        noValidate
      >
        <fieldset className="stack" disabled={!ready}>
          <legend className="sr-only">作成ガイドの入力</legend>
          {Object.keys(errors).length > 0 && (
            <div role="alert" className="notice">
              入力内容を確認してください。
            </div>
          )}
          {steps[step].map((field) => (
            <div key={field} className="field">
              <label htmlFor={field}>{labels[field]}</label>
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
              <p id={`${field}-error`} className="field-error">
                {errors[field]?.message}
              </p>
            </div>
          ))}
          <div className="actions">
            {step > 0 && (
              <Button
                type="button"
                variant="outline"
                onClick={() => setStep(step - 1)}
              >
                戻る
              </Button>
            )}
            <Button type="submit">
              {step === 2 ? "人格の確認へ" : "次へ"}
            </Button>
          </div>
        </fieldset>
      </form>
    </div>
  );
}
