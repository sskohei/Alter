import createClient from "openapi-fetch";
import type { paths } from "./schema";

export type { components, paths } from "./schema";
export const createApiClient = (baseUrl: string) => createClient<paths>({ baseUrl });

export { ApiError, authenticatedFetch, unwrap, pagination } from "./transport";
