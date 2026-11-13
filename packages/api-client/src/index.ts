import createClient from "openapi-fetch";
import type { paths } from "./schema";

export type { components, paths } from "./schema";
export const createApiClient = (baseUrl: string, fetcher?: typeof fetch) => createClient<paths>({ baseUrl, fetch: fetcher });

export { ApiError, authenticatedFetch, unwrap, pagination } from "./transport";
