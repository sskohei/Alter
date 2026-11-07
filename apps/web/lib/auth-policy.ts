export function safeReturnPath(value: unknown) {
  if (typeof value !== "string") return "/dashboard";
  // Restrict to implemented routes; disallow protocol-relative URLs and encodings.
  return /^\/(dashboard|onboarding|agent(?:\/new|\/edit)?)$/.test(value) ? value : "/dashboard";
}
export function accessMessage(code: string) {
  const messages: Record<string, string> = {
    INVITATION_REQUIRED: "招待が確認できません。招待元へお問い合わせください。",
    ACCOUNT_INACTIVE: "アカウントは停止中、または削除手続き中です。招待時の私的な連絡先へお問い合わせください。",
    AUTHORIZATION_UNAVAILABLE: "利用資格を確認できませんでした。時間をおいて再試行してください。",
    oauth: "ログインを完了できませんでした。キャンセルした場合も、もう一度お試しください。",
    expired: "ログインの有効期限が切れました。もう一度ログインしてください。",
    consent: "利用説明を確認し、同意を選択してください。",
    configuration: "ログイン設定を確認中です。運営へお問い合わせください。",
  };
  return messages[code] ?? "操作を完了できませんでした。もう一度お試しください。";
}
