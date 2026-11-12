// Unapproved #7 contract. Keep distinct from the live /health client.
import createClient from "openapi-fetch";
import type { paths } from "./preview-schema";
export type { paths, components } from "./preview-schema";
export { ApiError, authenticatedFetch, unwrap, pagination } from "./transport";
export const createPreviewClient = (baseUrl: string, fetcher?: typeof fetch) =>
  createClient<paths>({ baseUrl, fetch: fetcher });
