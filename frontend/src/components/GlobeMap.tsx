"use client";

import { useEffect, useRef, useState } from "react";
import Globe, { GlobeMethods } from "react-globe.gl";
import * as THREE from "three";
import type { MapMarker, MapArc, MapData } from "@/lib/api";
import { useRouter } from "next/navigation";

export default function GlobeMap({ data }: { data: MapData }) {
  const globeEl = useRef<GlobeMethods | undefined>(undefined);
  const [windowSize, setWindowSize] = useState({ width: 800, height: 600 });
  const router = useRouter();

  useEffect(() => {
    // Handle resize
    const updateSize = () => {
      // Find parent container dimensions to make globe responsive
      const container = document.getElementById("globe-container");
      if (container) {
        setWindowSize({ width: container.clientWidth, height: container.clientHeight });
      } else {
        setWindowSize({ width: window.innerWidth, height: window.innerHeight * 0.7 });
      }
    };
    
    window.addEventListener("resize", updateSize);
    // Give time for layout to settle
    setTimeout(updateSize, 100);
    
    return () => window.removeEventListener("resize", updateSize);
  }, []);

  useEffect(() => {
    // Give the globe a slow spin
    if (globeEl.current) {
      globeEl.current.controls().autoRotate = true;
      globeEl.current.controls().autoRotateSpeed = 0.5;
    }
  }, []);

  // Format HTML for marker tooltip
  const getTooltipHtml = (d: object) => {
    const m = d as MapMarker;
    return `
      <div style="background: rgba(255,255,255,0.95); padding: 12px; border-radius: 12px; border: 1px solid #e1e3e1; box-shadow: 0 4px 12px rgba(0,0,0,0.1); color: #2d332f; font-family: sans-serif; min-width: 150px;">
        <div style="font-weight: 700; font-size: 15px; margin-bottom: 4px; color: #1a1f1c;">${m.name}</div>
        <div style="display: flex; align-items: center; gap: 6px; font-size: 12px; color: #878c88; margin-bottom: 6px;">
          <span style="background: ${m.gender === 'Male' ? '#e0f2fe' : m.gender === 'Female' ? '#fae8ff' : '#f4f5f4'}; padding: 2px 6px; border-radius: 4px; color: #2d332f;">${m.gender || 'Unknown'}</span>
          <span>${m.isAlive ? '<span style="color:#10b981;">● Living</span>' : 'Deceased'}</span>
        </div>
        <div style="font-size: 13px; font-weight: 500; color: #878c88; margin-bottom: 2px;">
          ${m.type === 'residence' ? 'Current Residence' : 'Burial Site'}
        </div>
        <div style="font-size: 12px; color: #5a5f5c;">
          ${m.city ? m.city : ''}${m.city && m.country ? ', ' : ''}${m.country ? m.country : ''}
          ${m.location ? m.location : ''}
        </div>
        <div style="margin-top: 8px; font-size: 11px; font-weight: 600; color: #c9956c;">Click to view profile &rarr;</div>
      </div>
    `;
  };

  return (
    <div id="globe-container" className="w-full h-full relative flex items-center justify-center bg-[#0f172a] overflow-hidden rounded-2xl" style={{ minHeight: "500px" }}>
      <Globe
        ref={globeEl}
        width={windowSize.width}
        height={windowSize.height}
        globeImageUrl="//unpkg.com/three-globe/example/img/earth-blue-marble.jpg"
        bumpImageUrl="//unpkg.com/three-globe/example/img/earth-topology.png"
        backgroundImageUrl="//unpkg.com/three-globe/example/img/night-sky.png"
        
        // Markers (Points)
        pointsData={data.markers}
        pointLat="lat"
        pointLng="lng"
        pointColor={(d: object) => (d as MapMarker).type === "residence" ? "#38bdf8" : "#fb923c"}
        pointAltitude={0.02}
        pointRadius={0.4}
        pointsMerge={true}
        pointResolution={32}
        
        // Custom HTML labels for points (if wanted, but let's stick to tooltips for cleaner look)
        pointLabel={getTooltipHtml}
        onPointClick={(pt: object) => {
          const m = pt as MapMarker;
          router.push(`/member/${m.id}`);
        }}

        // Arcs (Connections)
        arcsData={data.arcs}
        arcStartLat="startLat"
        arcStartLng="startLng"
        arcEndLat="endLat"
        arcEndLng="endLng"
        arcColor={(d: object) => (d as MapArc).color || "#c9956c"}
        arcDashLength={0.4}
        arcDashGap={0.2}
        arcDashAnimateTime={1500}
        arcAltitudeAutoScale={0.3}
        arcStroke={0.5}
        arcLabel={(d: object) => `<div style="background:rgba(0,0,0,0.7);color:white;padding:4px 8px;border-radius:4px;font-size:12px;">${(d as MapArc).label}</div>`}
      />
      
      {/* Legend overlay */}
      <div className="absolute bottom-6 left-6 bg-white/90 backdrop-blur-sm p-4 rounded-xl shadow-lg border border-white/20 z-10">
        <h4 className="font-serif font-bold text-sm mb-3">Map Legend</h4>
        <div className="space-y-2 text-xs">
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full bg-[#38bdf8] border-2 border-white pointer-events-none"></div>
            <span>Current Residence</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full bg-[#fb923c] border-2 border-white pointer-events-none"></div>
            <span>Burial Site</span>
          </div>
          <div className="flex items-center gap-2 mt-3 pt-2 border-t border-gray-200">
            <div className="w-6 h-0.5 bg-[#c9956c]"></div>
            <span>Family Connections (Child &rarr; Parent)</span>
          </div>
        </div>
      </div>
    </div>
  );
}
