"use client";

import { useEffect, useState } from "react";

// Decorative local clock. Renders a placeholder on the server to avoid hydration mismatch.
export function Clock({ className }: { className?: string }) {
  const [time, setTime] = useState("--:--");
  useEffect(() => {
    const tick = () => setTime(new Date().toTimeString().slice(0, 5));
    tick();
    const id = setInterval(tick, 10_000);
    return () => clearInterval(id);
  }, []);
  return <span className={className} aria-hidden="true">{time}</span>;
}
