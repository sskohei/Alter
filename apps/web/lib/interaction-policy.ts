export type LlmDisclosure = { name: string; policyUrl: string };
// #14 must supply reviewed deployment configuration. No provider is guessed.
export function interactionDisclosure(): LlmDisclosure | null {
  const name = process.env.ALTER_LLM_NAME?.trim();
  const value = process.env.ALTER_LLM_DATA_POLICY_URL;
  if (!name || !value) return null;
  try {
    const url = new URL(value);
    if (
      url.username ||
      url.password ||
      !(
        url.protocol === "https:" ||
        (url.protocol === "http:" &&
          ["127.0.0.1", "localhost"].includes(url.hostname))
      )
    )
      return null;
    return { name, policyUrl: url.toString() };
  } catch {
    return null;
  }
}
