import { beforeEach, expect, test } from "vitest";
import {
  agentFormSchema,
  emptyValues,
  draftKey,
  readDraft,
  writeDraft,
  toDraft,
  type AgentFormValues,
} from "../lib/agent-form";
const valid: AgentFormValues = {
  ...emptyValues,
  name: "分身",
  personality: "穏やか",
  values_text: "誠実",
  interests: "読書、音楽",
  speaking_style: "丁寧",
  interaction_policy: "相手を尊重",
  shareable_info: "好きな本",
  restrictions: "なし",
  private_info: "保存しない秘密",
};
beforeEach(() => sessionStorage.clear());
test("draft restore scopes to owner/version and never stores private text", () => {
  writeDraft(draftKey("owner-one"), valid, 1);
  expect(JSON.stringify(sessionStorage)).not.toContain("保存しない秘密");
  expect(readDraft(draftKey("owner-two"), 1)).toBeNull();
  expect(readDraft(draftKey("owner-one"), 2)).toBeNull();
  expect(readDraft(draftKey("owner-one"), 1)).toMatchObject({ name: "分身" });
  expect(readDraft(draftKey("owner-one"), 1)).not.toHaveProperty(
    "private_info",
  );
});
test("invalid storage does not break form", () => {
  sessionStorage.setItem(draftKey("owner-one"), "broken");
  expect(readDraft(draftKey("owner-one"), 0)).toBeNull();
});
test("form bounds text, name and arrays before sending generated input", () => {
  expect(agentFormSchema.safeParse(valid).success).toBe(true);
  expect(toDraft(valid).interests).toEqual(["読書", "音楽"]);
  for (const invalid of [
    { name: "x".repeat(81) },
    { personality: "x".repeat(2001) },
    { interests: Array(21).fill("読書").join(",") },
  ])
    expect(agentFormSchema.safeParse({ ...valid, ...invalid }).success).toBe(
      false,
    );
});
