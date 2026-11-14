"use client";
import { useEffect, useState } from "react";
import { ApiError } from "@alter/api-client";

export function useRetryAfter() {
  const [deadline, setDeadline] = useState(0);
  const [now, setNow] = useState(0);
  useEffect(() => {
    if (!deadline) return;
    const timer = setInterval(() => {
      const current = Date.now();
      setNow(current);
      if (current >= deadline) clearInterval(timer);
    }, 250);
    return () => clearInterval(timer);
  }, [deadline]);
  const seconds = Math.max(0, Math.ceil((deadline - now) / 1000));
  function defer(cause: unknown) {
    if (cause instanceof ApiError && cause.status === 429) {
      const current = Date.now();
      setNow(current);
      setDeadline(current + (cause.retryAfterSeconds ?? 60) * 1000);
    }
  }
  return { seconds, defer };
}
