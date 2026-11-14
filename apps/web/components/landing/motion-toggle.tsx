"use client";

import { useEffect, useSyncExternalStore } from "react";

// Lets visitors stop every auto-playing animation on the landing page (WCAG 2.2.2).
// Defaults to "stopped" when the OS asks for reduced motion; the choice is remembered.
const KEY = "alter-motion";
const REDUCE = "(prefers-reduced-motion: reduce)";
const listeners = new Set<() => void>();
let memory: boolean | null = null; // fallback when localStorage is unavailable

function readMotion(): boolean {
  let saved: string | null = null;
  try { saved = localStorage.getItem(KEY); } catch { /* storage unavailable */ }
  if (saved) return saved === "on";
  if (memory !== null) return memory;
  return !matchMedia(REDUCE).matches;
}
function writeMotion(on: boolean) {
  memory = on;
  try { localStorage.setItem(KEY, on ? "on" : "off"); } catch { /* storage unavailable */ }
  listeners.forEach((notify) => notify());
}
function subscribe(notify: () => void) {
  listeners.add(notify);
  const media = matchMedia(REDUCE);
  media.addEventListener("change", notify);
  return () => { listeners.delete(notify); media.removeEventListener("change", notify); };
}

export function MotionToggle() {
  const on = useSyncExternalStore(subscribe, readMotion, () => true);
  useEffect(() => {
    document.documentElement.dataset.motion = on ? "on" : "off";
    document.querySelectorAll<SVGSVGElement>("svg[data-smil]").forEach((svg) => (on ? svg.unpauseAnimations?.() : svg.pauseAnimations?.()));
  }, [on]);
  return (
    <button type="button" className="lp-motion" aria-pressed={!on} onClick={() => writeMotion(!on)}>
      {/* Constant label + aria-pressed, so screen readers announce the state, not a new name. */}
      <span className="lp-motion-box" aria-hidden="true">{on ? "" : "✓"}</span>
      動きを止める
    </button>
  );
}
