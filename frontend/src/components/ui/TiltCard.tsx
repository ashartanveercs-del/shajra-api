"use client";

import { useRef, type ReactNode, type MouseEvent } from "react";

/**
 * 3D perspective tilt + glare on hover (vanilla-tilt style, dependency-free).
 * Wrap any card to give it a smooth pointer-reactive 3D tilt with a moving
 * light glare. Respects prefers-reduced-motion (falls back to flat).
 */
export default function TiltCard({
  children,
  className = "",
  maxTilt = 8,
  scale = 1.02,
}: {
  children: ReactNode;
  className?: string;
  maxTilt?: number;
  scale?: number;
}) {
  const ref = useRef<HTMLDivElement | null>(null);

  const handleMouseMove = (event: MouseEvent<HTMLDivElement>) => {
    const el = ref.current;
    if (!el) return;
    if (typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const rect = el.getBoundingClientRect();
    const px = (event.clientX - rect.left) / rect.width; // 0..1
    const py = (event.clientY - rect.top) / rect.height; // 0..1
    const rotateX = (0.5 - py) * maxTilt * 2;
    const rotateY = (px - 0.5) * maxTilt * 2;
    el.style.transform = `perspective(900px) rotateX(${rotateX.toFixed(2)}deg) rotateY(${rotateY.toFixed(2)}deg) scale(${scale})`;
    el.style.setProperty("--glare-x", `${(px * 100).toFixed(1)}%`);
    el.style.setProperty("--glare-y", `${(py * 100).toFixed(1)}%`);
  };

  const handleMouseLeave = () => {
    const el = ref.current;
    if (!el) return;
    el.style.transform = "perspective(900px) rotateX(0deg) rotateY(0deg) scale(1)";
  };

  return (
    <div
      ref={ref}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      className={`tilt-card ${className}`}
      style={{ transformStyle: "preserve-3d", transition: "transform 0.15s ease-out" }}
    >
      {children}
    </div>
  );
}
