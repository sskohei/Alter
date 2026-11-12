import { z } from "zod";

const number = z.number({ error: "数値を入力してください。" })
  .finite("有限の数値を入力してください。")
  .nonnegative("0以上を入力してください。");
const budget = number.refine(
  value => Number(value.toFixed(6)) === value,
  "小数は6桁までで入力してください。",
);
export const interactionBudgetSchema = z.object({
  frequency_per_day: number.int("整数を入力してください。").max(2, "1日2回までです。"),
  daily_budget_usd: budget,
  max_session_usd: budget,
}).refine(values => values.max_session_usd <= values.daily_budget_usd, {
  path: ["max_session_usd"],
  message: "1会話の費用上限は日次予算以下にしてください。",
});
export type BudgetValues = z.infer<typeof interactionBudgetSchema>;
