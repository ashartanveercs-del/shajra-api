"use client";

import { useLayoutEffect, useRef } from "react";

/**
 * Animated count-up number. Starts counting when it scrolls into view.
 * Dependency-free (requestAnimationFrame + IntersectionObserver).
 */
export default function AnimatedCounter({
  to,
  duration = 1400,
  suffix = "",
}: {
  to: number;
  duration?: number;
  suffix?: string;
}) {
  const ref = useRef<HTMLSpanElement | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const reducedMotion =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reducedMotion || typeof IntersectionObserver !== "function") {
      return;
    }

    el.textContent = `0${suffix}`;
    let raf = 0;
    let started = false;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !started) {
          started = true;
          const start = performance.now();
          const tick = (now: number) => {
            const progress = Math.min((now - start) / duration, 1);
            const eased = 1 - Math.pow(1 - progress, 3); // easeOutCubic
            el.textContent = `${Math.round(to * eased)}${suffix}`;
            if (progress < 1) raf = requestAnimationFrame(tick);
          };
          raf = requestAnimationFrame(tick);
          observer.disconnect();
        }
      },
      { threshold: 0.5 },
    );
    observer.observe(el);
    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
    };
  }, [to, duration, suffix]);

  return <span ref={ref}>{`${to}${suffix}`}</span>;
}
