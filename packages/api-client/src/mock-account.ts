import type { components } from "./preview-schema";
type Profile = components["schemas"]["Profile"];
export function createAccountMock(
  getProfile: () => Profile,
  setProfile: (value: Profile) => void,
) {
  let deletion: components["schemas"]["DeletionRequest"] | null = null;
  return async (request: Request): Promise<Response | null> => {
    const path = new URL(request.url).pathname;
    if (path === "/api/v1/users/me/deletion") {
      if (request.method === "POST") {
        const body = await request.json();
        if (body.confirm !== true) return new Response(null, { status: 422 });
        if (!deletion)
          deletion = {
            id: crypto.randomUUID(),
            status: "pending",
            requested_at: new Date().toISOString(),
            completed_at: null,
          };
        return Response.json(deletion, { status: 202 });
      }
      if (!deletion) return new Response(null, { status: 404 });
      const next = {
        pending: "running",
        running: "completed",
        completed: "completed",
        failed: "failed",
      } as const;
      deletion = {
        ...deletion,
        status: next[deletion.status],
        completed_at:
          deletion.status === "running"
            ? new Date().toISOString()
            : deletion.completed_at,
      };
      return Response.json(deletion);
    }
    if (deletion)
      return Response.json(
        {
          error: {
            code: "ACCOUNT_INACTIVE",
            message: "Account inactive",
            request_id: crypto.randomUUID(),
          },
        },
        { status: 403 },
      );
    if (path === "/api/v1/preferences") {
      const profile = getProfile();
      if (request.method === "PATCH") {
        const body = await request.json();
        if (
          !Object.keys(body).length ||
          Object.entries(body).some(
            ([key, value]) =>
              !["web_notifications", "discord_dm_notifications"].includes(
                key,
              ) || typeof value !== "boolean",
          )
        )
          return new Response(null, { status: 422 });
        setProfile({ ...profile, ...body });
      }
      const next = getProfile();
      return Response.json({
        web_notifications: next.web_notifications,
        discord_dm_notifications: next.discord_dm_notifications,
      });
    }
    return null;
  };
}
