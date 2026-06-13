"use client";

import { useEffect, useRef } from "react";

/**
 * Animates a number from 0 to value when scrolled into view. The final value is
 * server-rendered, so it's correct without JS and for crawlers; the animation
 * rewinds to 0 and counts up once visible. Respects prefers-reduced-motion.
 */
export function CountUp({
  value,
  decimals = 0,
  className,
}: {
  value: number;
  decimals?: number;
  className?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const format = (n: number) =>
      decimals > 0 ? n.toFixed(decimals) : Math.round(n).toLocaleString("en-US");

    let raf = 0;
    const io = new IntersectionObserver(
      (entries) => {
        if (!entries.some((e) => e.isIntersecting)) return;
        io.disconnect();
        const t0 = performance.now();
        const duration = 1300;
        const tick = (t: number) => {
          const p = Math.min(1, (t - t0) / duration);
          const ease = 1 - Math.pow(1 - p, 3);
          el.textContent = format(value * ease);
          if (p < 1) raf = requestAnimationFrame(tick);
        };
        el.textContent = format(0);
        raf = requestAnimationFrame(tick);
      },
      { rootMargin: "-40px" },
    );
    io.observe(el);
    return () => {
      io.disconnect();
      cancelAnimationFrame(raf);
    };
  }, [value, decimals]);

  return (
    <span ref={ref} className={className}>
      {decimals > 0 ? value.toFixed(decimals) : value.toLocaleString("en-US")}
    </span>
  );
}
