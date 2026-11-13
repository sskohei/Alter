import { ApiError, unwrap } from "@alter/api-client/preview";
import { serverApi } from "./api-server";
export async function ownedAgent() {
  const { client } = await serverApi();
  try {
    return await unwrap(await client.GET("/api/v1/agents/me"));
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 404) return null;
    throw cause;
  }
}
export async function ownedPersonality(id: string) {
  const { client } = await serverApi();
  return unwrap(
    await client.GET("/api/v1/agents/{id}/profile", {
      params: { path: { id } },
    }),
  );
}

export async function ownedAgentState() {
  try {
    const agent = await ownedAgent();
    return {
      ok: true as const,
      data: agent
        ? { agent, revision: await ownedPersonality(agent.id) }
        : null,
    };
  } catch {
    return { ok: false as const, data: null };
  }
}
