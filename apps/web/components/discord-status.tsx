"use client";
import { useCallback } from "react";
import { ApiError, unwrap } from "@alter/api-client";
import { activityApi } from "../lib/api-browser";
import { useResource } from "../lib/use-resource";
import { DataState } from "./data-state";

export function DiscordStatus() {
  const load = useCallback((signal: AbortSignal) =>
    activityApi().GET("/api/v1/discord/connection", { signal }).then(unwrap), []);
  const resource = useResource(load, 30_000);
  if (resource.error instanceof ApiError && resource.error.status === 404)
    return <p>Discord連携は現在利用できません。</p>;
  return <>
    <DataState {...resource} retry={resource.refresh} />
    {resource.data && <p>{resource.data.connected ? "連携済み" : "未連携"}</p>}
  </>;
}
