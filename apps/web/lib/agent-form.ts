import { z } from "zod";
import type { components } from "@alter/api-client/preview";
export type Agent = components["schemas"]["Agent"];
export type Personality = components["schemas"]["PublicPersonality"];
export type Revision = components["schemas"]["ProfileRevision"];
export type DraftInput = components["schemas"]["AgentDraftInput"];
const text = z
  .string()
  .trim()
  .min(1, "入力してください。")
  .max(2000, "2000文字以内で入力してください。");
const optionalText = z.string().max(2000, "2000文字以内で入力してください。");
const list = z
  .string()
  .refine(
    (value) =>
      value
        .split(/[,、\n]/)
        .map((s) => s.trim())
        .filter(Boolean).length <= 20,
    "20項目以内にしてください。",
  )
  .refine(
    (value) => value.split(/[,、\n]/).every((s) => s.trim().length <= 2000),
    "各項目を2000文字以内にしてください。",
  );
export const agentFormSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "名前を入力してください。")
    .max(80, "80文字以内で入力してください。"),
  personality: text,
  values_text: text,
  interests: list.refine(
    (v) => v.trim().length > 0,
    "興味を入力してください。",
  ),
  speaking_style: text,
  interaction_policy: text,
  shareable_info: text,
  restrictions: text,
  hobbies: list,
  strengths: list,
  weaknesses: list,
  private_info: optionalText,
});
export type AgentFormValues = z.infer<typeof agentFormSchema>;
export const emptyValues: AgentFormValues = {
  name: "",
  personality: "",
  values_text: "",
  interests: "",
  speaking_style: "",
  interaction_policy: "",
  shareable_info: "",
  restrictions: "",
  hobbies: "",
  strengths: "",
  weaknesses: "",
  private_info: "",
};
export const labels: Record<keyof AgentFormValues, string> = {
  name: "AI分身の名前",
  personality: "性格",
  values_text: "大切にしている価値観",
  interests: "興味・関心",
  speaking_style: "話し方",
  interaction_policy: "交流の方針",
  shareable_info: "公開してよい情報",
  restrictions: "話してほしくない内容",
  hobbies: "趣味（任意）",
  strengths: "得意なこと（任意）",
  weaknesses: "苦手なこと（任意）",
  private_info: "非公開の補足（任意）",
};
export const publicFields = Object.keys(labels).filter(
  (k): k is Exclude<keyof AgentFormValues, "private_info"> =>
    k !== "private_info",
);
const split = (value: string) =>
  value
    .split(/[,、\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
export function toDraft(values: AgentFormValues): DraftInput {
  return {
    ...values,
    interests: split(values.interests),
    hobbies: split(values.hobbies),
    strengths: split(values.strengths),
    weaknesses: split(values.weaknesses),
  };
}
export function toForm(agent: Agent, revision: Personality): AgentFormValues {
  return {
    name: agent.name,
    personality: revision.personality,
    values_text: revision.values_text,
    interests: revision.interests.join("、"),
    speaking_style: revision.speaking_style,
    interaction_policy: revision.interaction_policy,
    shareable_info: revision.shareable_info,
    restrictions: revision.restrictions,
    hobbies: (revision.hobbies ?? []).join("、"),
    strengths: (revision.strengths ?? []).join("、"),
    weaknesses: (revision.weaknesses ?? []).join("、"),
    private_info: "",
  };
}
export function draftKey(owner: string, agentId?: string) {
  return `alter:draft:${owner}:${agentId ?? "new"}`;
}
export function writeDraft(
  key: string,
  values: Partial<AgentFormValues>,
  version: number,
) {
  try {
    const data = Object.fromEntries(
      publicFields.map((field) => [field, values[field] ?? ""]),
    );
    sessionStorage.setItem(key, JSON.stringify({ version, data }));
  } catch {
    /* Disabled/full storage: the form remains usable. */
  }
}
export function readDraft(
  key: string,
  version: number,
): Partial<AgentFormValues> | null {
  try {
    const stored = JSON.parse(sessionStorage.getItem(key) ?? "null");
    if (stored?.version !== version || !stored.data) return null;
    return Object.fromEntries(
      publicFields
        .filter((field) => typeof stored.data[field] === "string")
        .map((field) => [field, stored.data[field]]),
    );
  } catch {
    return null;
  }
}
export function removeDraft(key: string) {
  try {
    sessionStorage.removeItem(key);
  } catch {
    /* Disabled storage. */
  }
}
