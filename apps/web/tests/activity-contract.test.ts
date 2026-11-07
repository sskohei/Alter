// @vitest-environment node
import { expect, test } from "vitest";
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import schema from "../../../packages/api-client/preview.openapi.json";
import { createMockFetch } from "@alter/api-client/mock";
import { createPreviewClient, unwrap } from "@alter/api-client/preview";
const ajv = new Ajv2020({ strict: false });
addFormats(ajv);
ajv.addSchema(schema, "preview");
function validate(name: string, value: unknown) {
  const check = ajv.compile({ $ref: `preview#/components/schemas/${name}` });
  expect(check(value), JSON.stringify(check.errors)).toBe(true);
}
test("activity fixtures, opaque cursor and read persistence follow generated contract", async () => {
  const client = createPreviewClient(
    "http://mock.test",
    createMockFetch("normal", { activity: true }),
  );
  client.use({
    onRequest: ({ request }) => {
      request.headers.set("Authorization", "Bearer synthetic");
      return request;
    },
  });
  const first = await unwrap(
    await client.GET("/api/v1/conversations", {
      params: { query: { limit: 10 } },
    }),
  );
  validate("ConversationPage", first);
  expect(first.next_cursor).toBeTruthy();
  const next = await unwrap(
    await client.GET("/api/v1/conversations", {
      params: { query: { limit: 10, cursor: first.next_cursor! } },
    }),
  );
  validate("ConversationPage", next);
  expect(next.items).toHaveLength(2);
  expect(next.items.every((c) => !first.items.some((f) => f.id === c.id))).toBe(
    true,
  );
  const detail = await unwrap(
    await client.GET("/api/v1/conversations/{id}", {
      params: { path: { id: first.items[0].id } },
    }),
  );
  validate("ConversationDetail", detail);
  expect(detail.messages.map((m) => m.turn_no)).toEqual([1, 2]);
  const notices = await unwrap(await client.GET("/api/v1/notifications"));
  validate("NotificationPage", notices);
  const read = await unwrap(
    await client.PATCH("/api/v1/notifications/{id}/read", {
      params: { path: { id: notices.items[0].id } },
    }),
  );
  validate("Notification", read);
  expect(
    (await unwrap(await client.GET("/api/v1/notifications"))).items[0].read_at,
  ).toBeTruthy();
  validate(
    "DiscordConnection",
    await unwrap(await client.GET("/api/v1/discord/connection")),
  );
});
