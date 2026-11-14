import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ApiError } from "@alter/api-client/preview";
import { useResource } from "../lib/use-resource";
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});
const flush = () =>
  act(async () => {
    await Promise.resolve();
  });
test("poll interval, completion and cleanup do not overlap requests", async () => {
  vi.useFakeTimers();
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  const loader = vi.fn(async () => ({ running: true }));
  const poll = (data: { running: boolean }) => data.running;
  const { result, unmount } = renderHook(() =>
    useResource(loader, 15_000, poll),
  );
  await flush();
  expect(loader).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(14_999);
  });
  expect(loader).toHaveBeenCalledTimes(1);
  loader.mockResolvedValue({ running: false });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(loader).toHaveBeenCalledTimes(2);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30_000);
  });
  expect(loader).toHaveBeenCalledTimes(2);
  expect(result.current.data).toEqual({ running: false });
  unmount();
  expect(vi.getTimerCount()).toBe(0);
});
test("late responses cannot replace another resource; old request is aborted", async () => {
  let oldResolve!: (value: string) => void;
  let oldSignal!: AbortSignal;
  const old = (signal: AbortSignal) => {
    oldSignal = signal;
    return new Promise<string>((resolve) => {
      oldResolve = resolve;
    });
  };
  const fresh = async () => "fresh";
  const { result, rerender } = renderHook(({ loader }) => useResource(loader), {
    initialProps: { loader: old },
  });
  await flush();
  rerender({ loader: fresh });
  await flush();
  expect(oldSignal.aborted).toBe(true);
  expect(result.current.data).toBe("fresh");
  await act(async () => oldResolve("old private data"));
  expect(result.current.data).toBe("fresh");
});
test("429 honors Retry-After for polling and manual retry", async () => {
  vi.useFakeTimers();
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  const loader = vi
    .fn<() => Promise<string>>()
    .mockRejectedValueOnce(
      new ApiError(429, { code: "RATE_LIMITED", message: "wait" }, 60),
    )
    .mockResolvedValue("ok");
  const { result } = renderHook(() => useResource(loader, 30_000));
  await flush();
  act(() => result.current.refresh());
  await flush();
  expect(loader).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30_000);
  });
  expect(loader).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30_000);
  });
  expect(result.current.data).toBe("ok");
});
test("404 clears previously visible content", async () => {
  const loader = vi
    .fn<() => Promise<string>>()
    .mockResolvedValueOnce("private conversation")
    .mockRejectedValueOnce(
      new ApiError(404, { code: "NOT_FOUND", message: "missing" }),
    );
  const { result } = renderHook(() => useResource(loader));
  await flush();
  expect(result.current.data).toBe("private conversation");
  act(() => result.current.refresh());
  await flush();
  expect(result.current.data).toBeNull();
  expect(result.current.error).toBeInstanceOf(ApiError);
});
