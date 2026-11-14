"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@alter/api-client/preview";

type Loader<T> = (signal: AbortSignal) => Promise<T>;
type State<T> = {
  loader: Loader<T>;
  data: T | null;
  error: unknown;
  loaded: boolean;
};
// No module/global cache. Changing owner, resource ID or cursor discards old data.
export function useResource<T>(
  loader: Loader<T>,
  pollMs: number | null = null,
  shouldPoll?: (data: T) => boolean,
) {
  const [state, setState] = useState<State<T>>({
    loader,
    data: null,
    error: null,
    loaded: false,
  });
  const operations = useRef<{
    refresh: () => void;
    update: (recipe: (data: T) => T) => void;
  } | null>(null);
  useEffect(() => {
    let active = true;
    let sequence = 0;
    let latest: T | null = null;
    let controller: AbortController | null = null;
    let retryAt = 0;
    function run() {
      if (!active || controller || Date.now() < retryAt) return;
      const id = ++sequence;
      const request = new AbortController();
      controller = request;
      void Promise.resolve()
        .then(() => loader(request.signal))
        .then((data) => {
          if (!active || id !== sequence || request.signal.aborted) return;
          latest = data;
          setState({ loader, data, error: null, loaded: true });
        })
        .catch((error) => {
          if (!active || id !== sequence || request.signal.aborted) return;
          latest = null;
          if (error instanceof ApiError && error.status === 429)
            retryAt = Date.now() + (error.retryAfterSeconds ?? 60) * 1000;
          setState({ loader, data: null, error, loaded: true });
        })
        .finally(() => {
          if (id === sequence) controller = null;
        });
    }
    operations.current = {
      refresh: run,
      update: (recipe) => {
        sequence++;
        controller?.abort();
        controller = null;
        if (latest !== null) {
          latest = recipe(latest);
          setState({ loader, data: latest, error: null, loaded: true });
        }
      },
    };
    run();
    const timer =
      pollMs === null
        ? null
        : setInterval(() => {
            if (
              document.visibilityState !== "hidden" &&
              (latest === null || !shouldPoll || shouldPoll(latest))
            )
              run();
          }, pollMs);
    return () => {
      active = false;
      sequence++;
      controller?.abort();
      if (timer !== null) clearInterval(timer);
      operations.current = null;
    };
  }, [loader, pollMs, shouldPoll]);
  const refresh = useCallback(() => operations.current?.refresh(), []);
  const update = useCallback(
    (recipe: (data: T) => T) => operations.current?.update(recipe),
    [],
  );
  const current =
    state.loader === loader
      ? state
      : { data: null, error: null, loaded: false };
  return {
    data: current.data,
    error: current.error,
    loading: !current.loaded,
    refresh,
    update,
  };
}

export function useCursor() {
  const [history, setHistory] = useState<(string | null)[]>([null]);
  return {
    cursor: history.at(-1)!,
    hasPrevious: history.length > 1,
    next: (cursor: string) => setHistory((previous) => [...previous, cursor]),
    previous: () =>
      setHistory((previous) =>
        previous.length > 1 ? previous.slice(0, -1) : previous,
      ),
  };
}
