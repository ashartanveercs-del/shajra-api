"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Globe, { type GlobeMethods } from "react-globe.gl";

import type { MapArc, MapData, MapMarker } from "@/lib/api";

function escapeHtml(value: string | undefined): string {
  return (value ?? "").replace(
    /[&<>"']/g,
    (character) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;",
      })[character] ?? character,
  );
}

function markerTooltip(marker: MapMarker): string {
  const location = marker.location || [marker.city, marker.country].filter(Boolean).join(", ");
  const genderColor =
    marker.gender === "Male" ? "#e0f2fe" : marker.gender === "Female" ? "#fae8ff" : "#f4f5f4";

  return `
    <div style="background:rgba(255,255,255,0.95);padding:12px;border-radius:8px;border:1px solid #e1e3e1;box-shadow:0 4px 12px rgba(0,0,0,0.1);color:#2d332f;font-family:sans-serif;min-width:150px">
      <div style="font-weight:700;font-size:15px;margin-bottom:4px;color:#1a1f1c">${escapeHtml(marker.name)}</div>
      <div style="display:flex;align-items:center;gap:6px;font-size:12px;color:#878c88;margin-bottom:6px">
        <span style="background:${genderColor};padding:2px 6px;border-radius:4px;color:#2d332f">${escapeHtml(marker.gender || "Unknown")}</span>
        <span>${marker.isAlive ? '<span style="color:#10b981">Living</span>' : "Deceased"}</span>
      </div>
      <div style="font-size:13px;font-weight:500;color:#878c88;margin-bottom:2px">${marker.type === "residence" ? "Current Residence" : "Burial Site"}</div>
      <div style="font-size:12px;color:#5a5f5c">${escapeHtml(location)}</div>
      <div style="margin-top:8px;font-size:11px;font-weight:600;color:#c9956c">Click to view profile &rarr;</div>
    </div>
  `;
}

export default function GlobeMap({ data }: { data: MapData }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const globeRef = useRef<GlobeMethods | undefined>(undefined);
  const [size, setSize] = useState({ width: 1, height: 1 });
  const [rendererReady, setRendererReady] = useState(false);
  const router = useRouter();

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let active = true;

    const updateSize = (observedWidth?: number, observedHeight?: number) => {
      if (!active) return;
      const bounds = container.getBoundingClientRect();
      setSize({
        width: Math.max(1, Math.floor(observedWidth || bounds.width || container.clientWidth)),
        height: Math.max(1, Math.floor(observedHeight || bounds.height || container.clientHeight)),
      });
    };

    updateSize();
    if (typeof ResizeObserver === "undefined") {
      const handleWindowResize = () => updateSize();
      window.addEventListener("resize", handleWindowResize);
      return () => {
        active = false;
        window.removeEventListener("resize", handleWindowResize);
      };
    }

    const observer = new ResizeObserver((entries) => {
      const bounds = entries[0]?.contentRect;
      updateSize(bounds?.width, bounds?.height);
    });
    observer.observe(container);
    return () => {
      active = false;
      observer.disconnect();
    };
  }, []);

  const handleReady = () => {
    const controls = globeRef.current?.controls();
    if (controls) {
      controls.autoRotate = true;
      controls.autoRotateSpeed = 0.5;
    }
    setRendererReady(true);
  };

  return (
    <div
      ref={containerRef}
      role="region"
      aria-label="Interactive heritage globe"
      className="relative flex h-full min-h-0 w-full items-center justify-center overflow-hidden rounded-lg bg-[#0f172a]"
    >
      <Globe
        ref={globeRef}
        width={size.width}
        height={size.height}
        onGlobeReady={handleReady}
        globeImageUrl="//unpkg.com/three-globe/example/img/earth-blue-marble.jpg"
        bumpImageUrl="//unpkg.com/three-globe/example/img/earth-topology.png"
        backgroundImageUrl="//unpkg.com/three-globe/example/img/night-sky.png"
        showAtmosphere
        atmosphereColor="#c4956a"
        atmosphereAltitude={0.2}
        pointsData={data.markers}
        pointLat="lat"
        pointLng="lng"
        pointColor={(point: object) => ((point as MapMarker).type === "residence" ? "#38bdf8" : "#fb923c")}
        pointAltitude={0.02}
        pointRadius={0.4}
        pointsMerge
        pointResolution={32}
        pointLabel={(point: object) => markerTooltip(point as MapMarker)}
        onPointClick={(point: object) => router.push(`/member/${(point as MapMarker).id}`)}
        arcsData={data.arcs}
        arcStartLat="startLat"
        arcStartLng="startLng"
        arcEndLat="endLat"
        arcEndLng="endLng"
        arcColor={(arc: object) => (arc as MapArc).color || "#c9956c"}
        arcDashLength={0.4}
        arcDashGap={0.2}
        arcDashAnimateTime={1500}
        arcAltitudeAutoScale={0.3}
        arcStroke={0.5}
        arcLabel={(arc: object) => `<div style="background:rgba(0,0,0,0.7);color:white;padding:4px 8px;border-radius:4px;font-size:12px">${escapeHtml((arc as MapArc).label)}</div>`}
      />

      {!rendererReady && (
        <div role="status" className="absolute inset-0 z-20 flex items-center justify-center bg-[#0f172a] text-sm font-medium text-white">
          Preparing interactive globe
        </div>
      )}

      <div className="absolute bottom-4 left-4 z-10 rounded-lg border border-white/20 bg-white/90 p-3 shadow-lg backdrop-blur-sm sm:bottom-6 sm:left-6 sm:p-4">
        <h4 className="mb-3 font-serif text-sm font-bold">Map Legend</h4>
        <div className="space-y-2 text-xs">
          <div className="flex items-center gap-2">
            <span className="h-3 w-3 rounded-full border-2 border-white bg-[#38bdf8]" />
            <span>Current Residence</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="h-3 w-3 rounded-full border-2 border-white bg-[#fb923c]" />
            <span>Burial Site</span>
          </div>
          <div className="mt-3 flex items-center gap-2 border-t border-gray-200 pt-2">
            <span className="h-0.5 w-6 bg-[#c9956c]" />
            <span>Family Connections</span>
          </div>
        </div>
      </div>
    </div>
  );
}
