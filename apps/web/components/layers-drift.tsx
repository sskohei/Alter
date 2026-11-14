"use client";
import { useEffect, useRef } from "react";
import knot from "../public/visuals/m-knot.png";
import bubble from "../public/visuals/s-bubble.png";
import capsule from "../public/visuals/m-capsule.png";
import star from "../public/visuals/m-star.png";

// Decorative 3D shapes drifting behind the LAYERS navigation (desktop only).
// Each one has a "home" (top→bottom, alternating left/right), wanders around it with a slowly
// changing heading, keeps away from the others and bounces softly off the column edges.
// Stops entirely with prefers-reduced-motion. Hidden from assistive tech.
const SHAPES = [knot, bubble, capsule, star];

type Drifter = {
  el: HTMLImageElement; size: number; i: number; x: number; y: number; a: number; sp: number;
  rot: number; vr: number; p1: number; p2: number; f1: number; f2: number; side: number; jitter: number;
};

export function LayersDrift() {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const root = box.current;
    if (!root) return;
    const els = Array.from(root.querySelectorAll("img"));
    const W = () => root.clientWidth;
    const H = () => root.clientHeight;
    const n = els.length;
    const items: Drifter[] = els.map((el, i) => {
      const size = 44 + Math.random() * 44;
      el.style.width = `${size}px`;
      const side = i % 2 === 0 ? 0.14 : 0.74;
      const jitter = (Math.random() - 0.5) * 30;
      return {
        el, size, i, side, jitter,
        x: W() * side - size / 2 + jitter, y: ((i + 0.5) / n) * H() - size / 2,
        a: Math.random() * 6.28, sp: 10 + Math.random() * 10, rot: Math.random() * 360,
        vr: (Math.random() - 0.5) * 16, p1: Math.random() * 99, p2: Math.random() * 99,
        f1: 0.1 + Math.random() * 0.12, f2: 0.04 + Math.random() * 0.06,
      };
    });
    const place = (o: Drifter, bob = 0) => {
      o.el.style.transform = `translate(${o.x}px, ${o.y + bob}px) rotate(${o.rot}deg)`;
    };
    items.forEach((o) => place(o));
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      if (!W()) return; // nav hidden (mobile)
      const t = now / 1000;
      for (const o of items) {
        const hx = W() * o.side - o.size / 2 + o.jitter;
        const hy = ((o.i + 0.5) / n) * H() - o.size / 2;
        o.a += (Math.sin(t * o.f1 + o.p1) * 0.9 + Math.sin(t * o.f2 + o.p2) * 0.6) * dt;
        let vx = Math.cos(o.a) * o.sp + (hx - o.x) * 0.05;
        let vy = Math.sin(o.a) * o.sp + (hy - o.y) * 0.05;
        for (const p of items) {
          if (p === o) continue;
          const dx = o.x - p.x, dy = o.y - p.y;
          const d = Math.hypot(dx, dy) || 1, min = (o.size + p.size) * 0.6;
          if (d < min) { vx += (dx / d) * (min - d) * 0.8; vy += (dy / d) * (min - d) * 0.8; }
        }
        o.x += vx * dt;
        o.y += vy * dt;
        const lo = -o.size * 0.35, hiX = W() - o.size * 0.65, hiY = H() - o.size * 0.7;
        if (o.x < lo || o.x > hiX) { o.a = Math.PI - o.a; o.x = Math.max(lo, Math.min(hiX, o.x)); }
        if (o.y < 0 || o.y > hiY) { o.a = -o.a; o.y = Math.max(0, Math.min(hiY, o.y)); }
        o.rot += o.vr * dt;
        place(o, Math.sin(t * 0.8 + o.p1) * 6);
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);
  return (
    <div className="layers-drift" ref={box} aria-hidden="true">
      {SHAPES.map((s) => (
        // eslint-disable-next-line @next/next/no-img-element
        <img key={s.src} src={s.src} alt="" width={s.width} height={s.height} />
      ))}
    </div>
  );
}
