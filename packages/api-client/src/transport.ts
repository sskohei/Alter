export type ApiErrorBody = { code: string; message: string; request_id?: string };

export class ApiError extends Error {
  constructor(public status: number, public detail: ApiErrorBody) {
    super(detail.message);
    this.name = "ApiError";
  }
}

export type Page<T> = { items: T[]; next_cursor: string | null };
export function pagination(cursor?: string | null, limit = 20) {
  return { limit: Math.max(1, Math.min(50, limit)), ...(cursor ? { cursor } : {}) };
}

export type AuthTransport = {
  accessToken: () => Promise<string | null>;
  refreshToken: () => Promise<string | null>;
  onUnauthenticated: () => void | Promise<void>;
  fetch?: typeof fetch;
};

// Use per-request transports on the server: never share a user's credentials.
// The caller supplies a Request so openapi-fetch retains generated input types.
export function authenticatedFetch(auth: AuthTransport): typeof fetch {
  return async (input, init) => {
    const original = new Request(input, init);
    const requestId = original.headers.get("X-Request-ID") ?? crypto.randomUUID();
    const send = async (token: string | null) => {
      const request = new Request(original.clone(), { cache: "no-store" });
      request.headers.set("X-Request-ID", requestId);
      if (token) request.headers.set("Authorization", `Bearer ${token}`);
      return (auth.fetch ?? globalThis.fetch)(request);
    };
    let response = await send(await auth.accessToken());
    if (response.status === 401) {
      let token: string | null = null;
      try { token = await auth.refreshToken(); } catch { /* Refresh failed; return to login. */ }
      if (token) response = await send(token);
      if (response.status === 401) await auth.onUnauthenticated();
    }
    return response;
  };
}

export async function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): Promise<T> {
  if (result.data !== undefined) return result.data;
  const error = result.error as { error?: ApiErrorBody } | undefined;
  throw new ApiError(result.response.status, {
    code: error?.error?.code ?? "UNAVAILABLE",
    message: error?.error?.message ?? "通信に失敗しました。もう一度お試しください。",
    request_id: error?.error?.request_id ?? result.response.headers.get("X-Request-ID") ?? undefined,
  });
}
