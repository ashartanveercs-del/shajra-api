"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { fetchMapMarkers, type MapData, type MapMarker } from "@/lib/api";
import { Loader2, MapPin, Globe2, Map as MapIcon } from "lucide-react";
import Link from "next/link";

// 2D Leaflet map
const MapContainer = dynamic(() => import("react-leaflet").then((mod) => mod.MapContainer), { ssr: false });
const TileLayer = dynamic(() => import("react-leaflet").then((mod) => mod.TileLayer), { ssr: false });
const Marker = dynamic(() => import("react-leaflet").then((mod) => mod.Marker), { ssr: false });
const Popup = dynamic(() => import("react-leaflet").then((mod) => mod.Popup), { ssr: false });

// 3D Globe map
const GlobeMap = dynamic(() => import("@/components/GlobeMap"), { ssr: false });

export default function MapPage() {
  const [mapData, setMapData] = useState<MapData>({ markers: [], arcs: [] });
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"all" | "residence" | "burial">("all");
  const [leafletReady, setLeafletReady] = useState(false);
  const [is3D, setIs3D] = useState(true);

  useEffect(() => {
    fetchMapMarkers()
      .then(setMapData)
      .catch(() => setMapData({ markers: [], arcs: [] }))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    import("leaflet").then((L) => {
      delete (L.Icon.Default.prototype as unknown as Record<string, unknown>)._getIconUrl;
      L.Icon.Default.mergeOptions({
        iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
        iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
        shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
      });
      setLeafletReady(true);
    });
  }, []);

  const markers = mapData.markers;
  const filteredMarkers =
    filter === "all" ? markers : markers.filter((m) => m.type === filter);

  const filteredData = {
    markers: filteredMarkers,
    arcs: filter === "all" ? mapData.arcs : [], // Only show arcs on "all"
  };

  const residenceCount = markers.filter((m) => m.type === "residence").length;
  const burialCount = markers.filter((m) => m.type === "burial").length;

  return (
    <div className="mx-auto max-w-6xl px-5 sm:px-8 py-12 sm:py-16">
      <div className="mb-8 animate-fadeInUp flex flex-col md:flex-row md:items-end justify-between gap-6">
        <div>
          <p className="text-accent text-sm font-medium uppercase tracking-wide mb-2 flex items-center gap-2">
            <span className="w-6 h-px bg-accent" />
            Geography
          </p>
          <h1 className="heading-serif text-3xl sm:text-4xl font-bold mb-3">
            Heritage Map
          </h1>
          <p className="text-text-muted text-base">
            See where your family lives and rests across the world.
          </p>
        </div>
        
        {/* View Toggle */}
        <div className="flex bg-bg-secondary p-1 rounded-xl shadow-inner self-start">
          <button
            onClick={() => setIs3D(false)}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${!is3D ? "bg-white text-accent shadow-sm" : "text-text-muted hover:text-text-primary"}`}
          >
            <MapIcon className="w-4 h-4" />
            2D Map
          </button>
          <button
            onClick={() => setIs3D(true)}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${is3D ? "bg-white text-accent shadow-sm" : "text-text-muted hover:text-text-primary"}`}
          >
            <Globe2 className="w-4 h-4" />
            3D Globe
          </button>
        </div>
      </div>

      {/* Filter */}
      <div className="flex items-center gap-2 mb-5">
        {[
          { key: "all", label: `All (${markers.length})` },
          { key: "residence", label: `Residences (${residenceCount})` },
          { key: "burial", label: `Burial Sites (${burialCount})` },
        ].map((tab) => (
          <button
            key={tab.key}
            onClick={() => setFilter(tab.key as typeof filter)}
            className={`px-4 py-2 rounded-lg text-[13px] font-medium transition-heritage ${
              filter === tab.key
                ? "bg-accent text-white"
                : "bg-bg-card text-text-muted border border-border hover:border-accent/30 hover:text-accent"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Map */}
      <div className="heritage-card overflow-hidden relative rendering-wrapper" style={{ height: "62vh" }}>
        {(loading || (!is3D && !leafletReady)) ? (
          <div className="flex items-center justify-center h-full">
            <Loader2 className="w-6 h-6 animate-spin text-accent" />
          </div>
        ) : filteredMarkers.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center px-4">
            <div className="w-14 h-14 rounded-2xl bg-bg-secondary flex items-center justify-center mb-4">
              <MapPin className="w-6 h-6 text-text-light" />
            </div>
            <h2 className="font-serif text-xl font-semibold mb-2">No Locations Yet</h2>
            <p className="text-text-muted text-sm max-w-sm">
              Add coordinates to member profiles to see them appear on the map.
            </p>
          </div>
        ) : is3D ? (
          <GlobeMap data={filteredData} />
        ) : (
          <MapContainer
            center={[30, 50]}
            zoom={3}
            style={{ height: "100%", width: "100%", zIndex: 0 }}
            scrollWheelZoom={true}
          >
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            {filteredMarkers.map((marker, i) => (
              <Marker key={`${marker.id}-${marker.type}-${i}`} position={[marker.lat, marker.lng]}>
                <Popup>
                  <div className="text-sm font-sans">
                    <div className="font-bold mb-1">{marker.name}</div>
                    <div className="text-gray-500 text-xs mb-1">
                      {marker.type === "residence" ? "Current Residence" : "Burial Site"}
                    </div>
                    {marker.city && <div className="text-xs">{marker.city}{marker.country ? `, ${marker.country}` : ""}</div>}
                    {marker.location && <div className="text-xs">{marker.location}</div>}
                    <Link href={`/member/${marker.id}`} className="text-blue-600 underline mt-1 block text-xs">
                      View Profile &rarr;
                    </Link>
                  </div>
                </Popup>
              </Marker>
            ))}
          </MapContainer>
        )}
      </div>
    </div>
  );
}
