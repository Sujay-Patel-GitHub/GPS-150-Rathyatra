// src/components/MapView/MapView.jsx
// Features:
//  - Reliable OSM + CartoDB tile layers (no API key, always work)
//  - Auto-zoom to vehicle (zoom 17)
//  - "Center on vehicle" button
//  - ⏺ Record / ⏹ Stop recording button
//  - Download route as CSV + save to Firebase

import { useEffect, useRef, useState, useCallback, useMemo, Fragment } from "react";
import {
  MapContainer,
  TileLayer,
  Marker,
  Popup,
  Polyline,
  Circle,
  useMap,
  useMapEvents,
  LayersControl,
  Tooltip,
  ZoomControl,
} from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { ref as fbRef, set } from "firebase/database";
import { db } from "../../lib/firebase";
import { DEFAULT_MAP_CENTER, DEFAULT_MAP_ZOOM, LANDMARKS, vehicleLabels } from "../../lib/constants";
import { fmtCoord, fmtSpeed, timeAgo } from "../../utils/formatters";
import { AnimatedMarker, AnimatedCircle } from "./AnimatedMarker";

const { BaseLayer } = LayersControl;

// ── Fix Leaflet default icon paths in Vite ──────────────────────────────────
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconUrl:       "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
  iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
  shadowUrl:     "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
});

// ── Google Maps/Uber-style rotating vehicle pin ──────────────────────────────
function makePin(color, isSelected, bearing = 0, isEstimated = false, vehicleId = "") {
  const scale = isSelected ? 1.25 : 1;
  const size = Math.round(40 * scale); // 40px bounding box
  
  const rot = bearing || 0;
  const labelNum = vehicleId ? (vehicleId.match(/\d+/) || [vehicleId])[0] : "";
  
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 40 40">
    <defs>
      <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
        <feDropShadow dx="0" dy="1.5" stdDeviation="1.5" flood-color="rgba(0,0,0,0.4)"/>
      </filter>
      <radialGradient id="beamGrad" cx="50%" cy="100%" r="100%">
        <stop offset="0%" stop-color="${color}" stop-opacity="0.6" />
        <stop offset="60%" stop-color="${color}" stop-opacity="0.2" />
        <stop offset="100%" stop-color="${color}" stop-opacity="0" />
      </radialGradient>
    </defs>
    
    ${isSelected ? `<circle cx="20" cy="20" r="19" fill="none" stroke="${color}" stroke-width="1.5" opacity="0.3" />` : ''}
    
    <g transform="rotate(${rot} 20 20)">
      <!-- Google Maps-style direction flashlight beam -->
      ${rot !== 0 ? `<path d="M20 20 L11.5 5.3 A17 17 0 0 1 28.5 5.3 Z" fill="url(#beamGrad)" />` : ''}
      
      <!-- Core navigation circle -->
      <circle cx="20" cy="20" r="8.5" fill="${color}" stroke="white" stroke-width="2" filter="url(#shadow)" />
      
      <!-- White navigation arrowhead -->
      <path fill="white" d="M20 14.5 l3.5 7 -3.5 -1.8 -3.5 1.8 Z" />
    </g>
    
    ${isEstimated ? `
      <circle cx="31" cy="9" r="5" fill="#f97316" stroke="white" stroke-width="1"/>
      <text x="31" y="12" font-family="system-ui, -apple-system, sans-serif" font-size="7.5" font-weight="900" fill="white" text-anchor="middle">!</text>
    ` : ''}
    ${labelNum ? `
    <g>
      <rect x="11" y="28" width="18" height="9" rx="2.5" fill="white" stroke="${color}" stroke-width="1" />
      <text x="20" y="35.5" font-family="system-ui, -apple-system, sans-serif" font-size="7.5" font-weight="900" fill="#111827" text-anchor="middle">${labelNum}</text>
    </g>
    ` : ''}
  </svg>`;

  return L.divIcon({
    html: svg,
    className: "",
    iconSize:    [size, size],
    iconAnchor:  [size / 2, size / 2],
    popupAnchor: [0, -size / 2 - 2],
  });
}

const TRAIL_COLORS = ["#4285F4", "#FF6D00", "#0F9D58", "#9C27B0"];

// ── Map controller — manual center ONLY, no auto-zoom ───────────────────────
// The map stays wherever the user has panned/zoomed.
// Only the "🎯 Center" button triggers a flyTo.
function MapController({ centerTarget, vehicles }) {
  const map = useMap();
  const vehiclesRef = useRef(vehicles);

  // Keep ref updated with latest vehicles telemetry
  useEffect(() => {
    vehiclesRef.current = vehicles;
  }, [vehicles]);

  useEffect(() => {
    if (!centerTarget) return;
    const v = vehiclesRef.current[centerTarget.id];
    if (v && typeof v.lat === "number") {
      const zoom = centerTarget.zoom || map.getZoom();
      map.flyTo([v.lat, v.lng], zoom, { animate: true, duration: 0.8 });
    }
  }, [centerTarget, map]); // Only fly to target when centerTarget changes, not on every tick

  return null;
}

// Rotates the map pane to support Course Up mode (similar to Android Auto navigation)
function AndroidAutoMapRotator({ selectedId, vehicles, enabled }) {
  const map = useMap();

  useEffect(() => {
    const pane = map.getPane("mapPane");
    if (!pane) return;

    if (!enabled || !selectedId) {
      pane.style.transform = "";
      return;
    }

    const vehicle = vehicles[selectedId];
    const bearing = vehicle?.bearing || 0;

    // Smoothly apply CSS rotation centered on the pane
    pane.style.transform = `rotate(${-bearing}deg)`;
    pane.style.transformOrigin = "center";
  }, [selectedId, vehicles, enabled, map]);

  return null;
}

// Follows the selected vehicle in real-time at zoom 18 when HUD is active
function AndroidAutoFollowController({ selectedId, vehicles, enabled }) {
  const map = useMap();

  useEffect(() => {
    if (!enabled || !selectedId) return;
    const v = vehicles[selectedId];
    if (v && typeof v.lat === "number" && typeof v.lng === "number") {
      map.setView([v.lat, v.lng], 18, { animate: true, duration: 0.5 });
    }
  }, [selectedId, vehicles, enabled, map]);

  return null;
}


function VehiclePopup({ vehicleId, data }) {
  return (
    <div style={{ fontFamily: "Inter, sans-serif", minWidth: 210, color: "#f1f5f9" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
        <span style={{ fontSize: 24 }}>🚌</span>
        <div>
          <p style={{ margin: 0, fontWeight: 800, fontSize: 13, color: "#fff", lineHeight: 1.2 }}>
            {data.display_name || vehicleId}
          </p>
          <p style={{
            margin: "2px 0 0",
            fontSize: 9,
            fontWeight: 800,
            textTransform: "uppercase",
            letterSpacing: "0.05em",
            color: data.status === "online" 
              ? "#4ade80" 
              : data.status === "weak"
              ? "#fbbf24"
              : data.status === "jammed"
              ? "#f87171"
              : "#9ca3af"
          }}>
            {data.status === "online" 
              ? "● Online" 
              : data.status === "weak" 
              ? "● Weak Signal" 
              : data.status === "jammed" 
              ? "● Jammed" 
              : data.status === "lost" 
              ? "○ Signal Low" 
              : "○ Offline"}
            {data.is_estimated && data.online && (
              <span style={{ color: "#fb923c", marginLeft: 8 }}>
                ⚠️ IMU Est.
              </span>
            )}
          </p>
        </div>
      </div>
      <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
        <tbody>
          {[
            ["Latitude",  fmtCoord(data.lat)],
            ["Longitude", fmtCoord(data.lng)],
            ["Speed",     fmtSpeed(data.speed)],
            ["Satellites", data.satellites !== undefined ? `${data.satellites} sats` : "—"],
            ["HDOP",      data.hdop !== undefined ? `${Number(data.hdop).toFixed(2)}` : "—"],
            ["Tracking", data.is_estimated ? "⚠️ IMU Estimated" : "🛰️ GPS Active"],
            ["Updated",   timeAgo(data.timestamp)],
          ].map(([l, v]) => (
            <tr key={l} style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
              <td style={{ color: "rgba(255,255,255,0.5)", padding: "6px 0", fontWeight: 600 }}>{l}</td>
              <td style={{ fontWeight: 700, color: "#fff", fontFamily: "monospace", textAlign: "right" }}>{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <a
        href={`https://maps.google.com/?q=${data.lat},${data.lng}`}
        target="_blank" rel="noopener noreferrer"
        style={{
          display: "block",
          marginTop: 14,
          padding: "8px 0",
          background: "linear-gradient(135deg, #f97316, #dc2626)",
          color: "white",
          borderRadius: 10,
          textAlign: "center",
          fontWeight: 700,
          fontSize: 11,
          textDecoration: "none",
          boxShadow: "0 4px 12px rgba(249, 115, 22, 0.2)",
          transition: "transform 0.15s"
        }}
      >
        📍 Open in Google Maps
      </a>
    </div>
  );
}

// ── Map Click Inspector ───────────────────────────────────────────────────────
// Shows exact GPS coordinates + OSRM road-snapped coordinates on every map click
function MapClickInspector({ enabled, onClickResult }) {
  useMapEvents({
    click(e) {
      if (!enabled) return;
      const { lat, lng } = e.latlng;
      onClickResult({ lat, lng, snapped: null, loading: true });

      // Query OSRM nearest to get exact road centerline coordinate
      const url = `https://router.project-osrm.org/nearest/v1/driving/${lng},${lat}?number=1`;
      fetch(url)
        .then((r) => r.json())
        .then((data) => {
          const wp = data?.waypoints?.[0];
          if (wp) {
            onClickResult({
              lat,
              lng,
              loading: false,
              snapped: {
                lat: wp.location[1],
                lng: wp.location[0],
                name: wp.name || "Unknown road",
                distance: wp.distance,
              },
            });
          } else {
            onClickResult({ lat, lng, loading: false, snapped: null });
          }
        })
        .catch(() => {
          onClickResult({ lat, lng, loading: false, snapped: null });
        });
    },
  });
  return null;
}

// ── Recording helpers ────────────────────────────────────────────────────────
const normalizePoints = (points) => {
  if (!points) return [];
  return points.map((p, i) => {
    if (Array.isArray(p)) {
      return {
        lat: p[0],
        lng: p[1],
        speed: 0,
        timestamp: new Date(Date.now() - (points.length - 1 - i) * 5000).toISOString()
      };
    }
    return {
      lat: p.lat ?? p[0],
      lng: p.lng ?? p[1],
      speed: p.speed ?? p.speed_kmh ?? 0,
      timestamp: p.timestamp ?? new Date().toISOString()
    };
  });
};

function downloadCSV(points, vehicleId, sessionId) {
  const normPoints = normalizePoints(points);
  const header = "index,latitude,longitude,speed_kmh,timestamp\n";
  const rows   = normPoints
    .map((p, i) =>
      `${i + 1},${p.lat},${p.lng},${p.speed ?? 0},${p.timestamp ?? ""}`)
    .join("\n");
  const blob = new Blob([header + rows], { type: "text/csv" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = `route_${vehicleId}_${sessionId}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function downloadGPX(points, vehicleId, sessionId) {
  const normPoints = normalizePoints(points);
  let gpx = `<?xml version="1.0" encoding="UTF-8"?>\n`;
  gpx += `<gpx version="1.1" creator="Rath Yatra GPS Tracker" xmlns="http://www.topografix.com/GPX/1/1">\n`;
  gpx += `  <metadata>\n`;
  gpx += `    <name>Route for ${vehicleId}</name>\n`;
  gpx += `    <time>${new Date().toISOString()}</time>\n`;
  gpx += `  </metadata>\n`;
  gpx += `  <trk>\n`;
  gpx += `    <name>${vehicleId} Breadcrumb Trail</name>\n`;
  gpx += `    <trkseg>\n`;
  
  normPoints.forEach((p) => {
    let tStr = p.timestamp;
    try {
      tStr = new Date(p.timestamp).toISOString();
    } catch (e) {}
    
    gpx += `      <trkpt lat="${p.lat}" lon="${p.lng}">\n`;
    gpx += `        <time>${tStr}</time>\n`;
    gpx += `        <speed>${(p.speed / 3.6).toFixed(2)}</speed>\n`;
    gpx += `      </trkpt>\n`;
  });
  
  gpx += `    </trkseg>\n`;
  gpx += `  </trk>\n`;
  gpx += `</gpx>`;
  
  const blob = new Blob([gpx], { type: "application/gpx+xml" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = `route_${vehicleId}_${sessionId}.gpx`;
  a.click();
  URL.revokeObjectURL(url);
}

function downloadKML(points, vehicleId, sessionId) {
  const normPoints = normalizePoints(points);
  let kml = `<?xml version="1.0" encoding="UTF-8"?>\n`;
  kml += `<kml xmlns="http://www.opengis.net/kml/2.2">\n`;
  kml += `  <Document>\n`;
  kml += `    <name>${vehicleId} Route - ${sessionId}</name>\n`;
  kml += `    <description>Rath Yatra GPS Tracker Breadcrumb Trail</description>\n`;
  kml += `    <Style id="yellowLineGreenPoly">\n`;
  kml += `      <LineStyle>\n`;
  kml += `        <color>7f00ffff</color>\n`;
  kml += `        <width>4</width>\n`;
  kml += `      </LineStyle>\n`;
  kml += `    </Style>\n`;
  kml += `    <Placemark>\n`;
  kml += `      <name>${vehicleId} Path</name>\n`;
  kml += `      <styleUrl>#yellowLineGreenPoly</styleUrl>\n`;
  kml += `      <LineString>\n`;
  kml += `        <extrude>1</extrude>\n`;
  kml += `        <tessellate>1</tessellate>\n`;
  kml += `        <altitudeMode>clampToGround</altitudeMode>\n`;
  kml += `        <coordinates>\n`;
  
  normPoints.forEach((p) => {
    kml += `          ${p.lng},${p.lat},0\n`;
  });
  
  kml += `        </coordinates>\n`;
  kml += `      </LineString>\n`;
  kml += `    </Placemark>\n`;
  kml += `  </Document>\n`;
  kml += `</kml>`;

  const blob = new Blob([kml], { type: "application/vnd.google-earth.kml+xml" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = `route_${vehicleId}_${sessionId}.kml`;
  a.click();
  URL.revokeObjectURL(url);
}

async function saveRouteToFirebase(points, vehicleId, sessionId) {
  try {
    const path = fbRef(db, `recordings/${vehicleId}/${sessionId}`);
    await set(path, {
      vehicleId,
      sessionId,
      startTime:  points[0]?.timestamp ?? "",
      endTime:    points[points.length - 1]?.timestamp ?? "",
      pointCount: points.length,
      points,
    });
    console.log("[Recording] Saved to Firebase:", `recordings/${vehicleId}/${sessionId}`);
    return true;
  } catch (e) {
    console.error("[Recording] Firebase save failed:", e);
    return false;
  }
}

function MapAssignClickHandler({ enabled, onAddPoint }) {
  useMapEvents({
    click(e) {
      if (!enabled) return;
      onAddPoint([e.latlng.lat, e.latlng.lng]);
    }
  });
  return null;
}

const makeAssignPin = (label) => {
  const isStart = label === "Start";
  const isEnd = label === "End";
  const bgColor = isStart ? "#10B981" : isEnd ? "#EF4444" : "#3B82F6";
  return L.divIcon({
    html: `<div style="background-color: ${bgColor}; color: white; font-weight: 800; font-size: 8px; border: 1.5px solid white; border-radius: 9999px; padding: 2px 5px; box-shadow: 0 2px 5px rgba(0,0,0,0.4); white-space: nowrap; display: inline-block; transform: translate(-50%, -50%);">${label}</div>`,
    className: "custom-assign-pin",
    iconSize: [0, 0]
  });
};

export function MapView({ 
  vehicles, 
  selectedId, 
  onVehicleSelect, 
  yatraRoute, 
  rawYatraRoute, 
  useSnapping, 
  onToggleSnapping, 
  useCompass, 
  onToggleCompass,
  isAndroidAuto = false,
  mapRotationMode = "course-up",
  onToggleAndroidAuto,
  frontBackRoutes = [],
  playbackMode = false,
  onTogglePlaybackMode = () => {},
  playbackIndex = 0,
  onPlaybackIndexChange = () => {},
  playbackIsPlaying = false,
  onPlaybackIsPlayingChange = () => {},
  playbackSpeed = 2,
  onPlaybackSpeedChange = () => {},
  processionAnalytics = null,
  playbackVehicleId = "",
  onPlaybackVehicleIdChange = () => {},
  playbackDate = "",
  onPlaybackDateChange = () => {},
  playbackStartTime = "",
  onPlaybackStartTimeChange = () => {},
  playbackEndTime = "",
  onPlaybackEndTimeChange = () => {},
  loadingPlayback = false,
  onLoadPlayback = () => {},
  selectedRecordingKey = "",
  onSelectedRecordingKeyChange = () => {},
  playbackRoutePoints = [],
  selectedRecordVehicleId = null,
  totalRawPoints = 0,
  deviceList = [],
}) {
  const vehicleIds  = Object.keys(vehicles);
  
  const formattedYatraRoute = useMemo(() => {
    if (!yatraRoute || yatraRoute.length === 0) return [];
    return yatraRoute.map(p => {
      if (Array.isArray(p)) return p;
      if (p && typeof p.lat === "number" && typeof p.lng === "number") {
        return [p.lat, p.lng];
      }
      return null;
    }).filter(Boolean);
  }, [yatraRoute]);

  const [centerTarget, setCenterTarget] = useState(null);
  const [selectedZoom, setSelectedZoom] = useState(17);
  const [hasAutoCentered, setHasAutoCentered] = useState(false);

  // ── GPS Inspector state ────────────────────────────────────────────────
  const [inspectorEnabled, setInspectorEnabled] = useState(false);
  const [inspectorResult, setInspectorResult] = useState(null);
  const [copiedField, setCopiedField] = useState(null);

  // ── Route Assigning Mode State ───────────────────────────────────────────
  const [assignMode, setAssignMode] = useState(false);
  const [assignPoints, setAssignPoints] = useState([]);
  const [fetchedRoute, setFetchedRoute] = useState([]);
  const [routeName, setRouteName] = useState("");
  const [assignStatus, setAssignStatus] = useState("");

  const handleAddAssignPoint = useCallback((latlng) => {
    setAssignPoints(prev => [...prev, latlng]);
  }, []);

  useEffect(() => {
    if (assignMode && assignPoints.length >= 1) {
      setFetchedRoute(assignPoints);
      setAssignStatus(assignPoints.length === 1 ? "Click map to set End point" : "Route updated point-to-point!");
    } else {
      setFetchedRoute([]);
      setAssignStatus("");
    }
  }, [assignPoints, assignMode]);

  const handleSaveRoute = useCallback(async () => {
    if (fetchedRoute.length === 0) return;
    setAssignStatus("Saving to MongoDB...");
    try {
      const res = await fetch("/api/save_route", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          route_name: routeName || `Assigned Route ${new Date().toLocaleDateString()}`,
          start_point: assignPoints[0],
          end_point: assignPoints[assignPoints.length - 1],
          points: fetchedRoute
        })
      });
      const data = await res.json();
      if (data.ok) {
        setAssignStatus("Saved successfully to MongoDB!");
        setTimeout(() => {
          setAssignMode(false);
          setAssignPoints([]);
          setFetchedRoute([]);
          setRouteName("");
          setAssignStatus("");
        }, 1500);
      } else {
        setAssignStatus("Failed to save: " + data.error);
      }
    } catch (e) {
      console.error(e);
      setAssignStatus("Error saving route");
    }
  }, [fetchedRoute, routeName, assignPoints]);


  const copyToClipboard = useCallback((text, field) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopiedField(field);
      setTimeout(() => setCopiedField(null), 1800);
    });
  }, []);

  // Auto-center on startup/mount when vehicle data becomes available
  useEffect(() => {
    if (hasAutoCentered) return;

    // Find a target vehicle: selected one, first online one, or any with valid coords
    const targetId = selectedId || 
                     vehicleIds.find(id => vehicles[id]?.online && typeof vehicles[id]?.lat === "number") ||
                     vehicleIds.find(id => typeof vehicles[id]?.lat === "number");

    if (targetId && vehicles[targetId] && typeof vehicles[targetId].lat === "number") {
      // eslint-disable-next-line react-hooks/exhaustive-deps
      setCenterTarget({ id: targetId, t: Date.now(), zoom: selectedZoom });
      setHasAutoCentered(true);
    }
  }, [vehicles, selectedId, vehicleIds, selectedZoom, hasAutoCentered]);

  // Fly to and zoom in on a vehicle when it is selected
  const lastSelectedIdRef = useRef("");
  useEffect(() => {
    if (selectedId && selectedId !== lastSelectedIdRef.current) {
      lastSelectedIdRef.current = selectedId;
      const v = vehicles[selectedId];
      if (v && typeof v.lat === "number") {
        setCenterTarget({ id: selectedId, t: Date.now(), zoom: 16 });
      }
    } else if (!selectedId) {
      lastSelectedIdRef.current = "";
    }
  }, [selectedId, vehicles]);

  // ── Recording state ─────────────────────────────────────────────────────
  const [recording,    setRecording]    = useState(false);
  const [recVehicleId, setRecVehicleId] = useState(null);
  const [recPoints,    setRecPoints]    = useState([]);
  const [recSeconds,   setRecSeconds]   = useState(0);
  const [recSaved,     setRecSaved]     = useState(false);
  const recTimerRef  = useRef(null);
  const recSessionId = useRef(null);

  // Watch for new positions while recording
  useEffect(() => {
    if (!recording || !recVehicleId) return;
    const v = vehicles[recVehicleId];
    if (!v || typeof v.lat !== "number") return;

    // eslint-disable-next-line react-hooks/exhaustive-deps
    setRecPoints((prev) => {
      const last = prev[prev.length - 1];
      const moved =
        !last ||
        Math.abs(last.lat - v.lat) > 0.000001 ||
        Math.abs(last.lng - v.lng) > 0.000001;
      if (!moved) return prev;
      return [...prev, { lat: v.lat, lng: v.lng, speed: v.speed, timestamp: v.timestamp }];
    });
  }, [vehicles, recording, recVehicleId]);

  // Elapsed time counter
  useEffect(() => {
    if (recording) {
      recTimerRef.current = setInterval(() => setRecSeconds((s) => s + 1), 1000);
    } else {
      clearInterval(recTimerRef.current);
    }
    return () => clearInterval(recTimerRef.current);
  }, [recording]);

  const startRecording = useCallback((vehicleId) => {
    const sid = new Date().toISOString().replace(/[:.]/g, "-");
    recSessionId.current = sid;
    setRecVehicleId(vehicleId);
    setRecPoints([]);
    setRecSeconds(0);
    setRecSaved(false);
    setRecording(true);
  }, []);

  const stopRecording = useCallback(async () => {
    setRecording(false);
    if (recPoints.length === 0) return;

    const sid = recSessionId.current;
    const vid = recVehicleId;

    // Save to Firebase
    await saveRouteToFirebase(recPoints, vid, sid);

    // Download CSV
    downloadCSV(recPoints, vid, sid);

    setRecSaved(true);
    setTimeout(() => setRecSaved(false), 4000);
  }, [recPoints, recVehicleId]);

  const fmt = (s) =>
    `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

  // Pick a default vehicle for recording controls
  const activeVehicle = selectedId || vehicleIds.find(
    (id) => vehicles[id]?.online
  );

  return (
    <div className="w-full h-full relative overflow-hidden">
      <MapContainer
        center={[DEFAULT_MAP_CENTER.lat, DEFAULT_MAP_CENTER.lng]}
        zoom={DEFAULT_MAP_ZOOM}
        style={{ width: "100%", height: "100%" }}
        zoomControl={false}
        preferCanvas={true}
      >
        {!isAndroidAuto && <ZoomControl position="bottomright" />}

        {/* ── Tile layers ────────────────────────────────────────────────── */}
        <LayersControl position="topright">

          <BaseLayer name="🗺️ OSM Street">
            <TileLayer
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
              maxZoom={19}
            />
          </BaseLayer>

          <BaseLayer checked name="🗺️ Google Streets">
            <TileLayer
              url="https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}"
              attribution="&copy; Google Maps"
              maxZoom={20}
            />
          </BaseLayer>

          <BaseLayer name="🛰️ Google Hybrid">
            <TileLayer
              url="https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}"
              attribution="&copy; Google Maps"
              maxZoom={20}
            />
          </BaseLayer>

          <BaseLayer name="🌙 Dark (CartoDB)">
            <TileLayer
              url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
              attribution='&copy; <a href="https://carto.com">CARTO</a>'
              subdomains="abcd"
              maxZoom={19}
            />
          </BaseLayer>

        </LayersControl>

        <MapController vehicles={vehicles} centerTarget={centerTarget} />

        {/* ── GPS Click Inspector ─────────────────────────────────────── */}
        <MapClickInspector
          enabled={inspectorEnabled}
          onClickResult={setInspectorResult}
        />

        {isAndroidAuto && (
          <>
            <AndroidAutoFollowController
              selectedId={selectedId}
              vehicles={vehicles}
              enabled={true}
            />
            {mapRotationMode === "course-up" && (
              <AndroidAutoMapRotator
                selectedId={selectedId}
                vehicles={vehicles}
                enabled={true}
              />
            )}
          </>
        )}




        {/* ── Route Assigning Drawing Overlays ──────────────────────────────── */}
        <MapAssignClickHandler
          enabled={assignMode}
          onAddPoint={handleAddAssignPoint}
        />

        {fetchedRoute.length > 0 && (
          <>
            {/* Reddish/purple corridor buffer around the snapped route */}
            <Polyline
              positions={fetchedRoute}
              pathOptions={{
                color: "#ef4444", // Reddish corridor
                weight: 80,
                opacity: 0.15,
                lineJoin: "round",
                lineCap: "round",
              }}
            />
            {/* Inner glowing blue route line */}
            <Polyline
              positions={fetchedRoute}
              pathOptions={{
                color: "#2563eb", // Deep blue snapped road line
                weight: 5,
                opacity: 0.9,
                lineJoin: "round",
                lineCap: "round",
              }}
            />
          </>
        )}

        {/* Individual clicked assign points markers */}
        {assignPoints.map((p, idx) => (
          <Marker
            key={idx}
            position={p}
            icon={makeAssignPin(idx === 0 ? "Start" : idx === assignPoints.length - 1 ? "End" : `Pt ${idx + 1}`)}
          >
            <Popup closeButton={false}>
              <div className="text-xs font-bold p-1">
                <span className="text-orange-500 font-extrabold uppercase">
                  {idx === 0 ? "Start Point" : idx === assignPoints.length - 1 ? "End Point" : `Waypoint ${idx + 1}`}
                </span>
                <div className="text-[10px] text-gray-500 font-mono mt-0.5">
                  Lat: {p[0].toFixed(6)}<br />Lng: {p[1].toFixed(6)}
                </div>
              </div>
            </Popup>
          </Marker>
        ))}

        {/* ── Geofenced Landmarks ─────────────────────────────────────────── */}
        {LANDMARKS.map((landmark) => (
          <Circle
            key={landmark.name}
            center={landmark.coords}
            radius={landmark.radius}
            pathOptions={{
              color: landmark.color,
              fillColor: landmark.color,
              fillOpacity: 0.1,
              weight: 1.5,
              dashArray: "6 4",
              opacity: 0.7
            }}
          >
            <Tooltip permanent direction="top" opacity={0.9} className="custom-geofence-tooltip">
              <span className="font-bold text-[9px] text-gray-950 px-1 py-0.5">🏰 {landmark.name}</span>
            </Tooltip>
          </Circle>
        ))}

        {/* ── Route trails ───────────────────────────────────────────────── */}
        {vehicleIds.map((vehicleId, idx) => {
          const data = vehicles[vehicleId];
          if (!data) return null;
          const trail = data.trail || [];
          const rawTrail = data.rawTrail || [];
          const isSelected = selectedId === vehicleId;
          const color = TRAIL_COLORS[idx % TRAIL_COLORS.length];

          return (
            <Fragment key={`trails-${vehicleId}`}>
              {/* Raw GPS Trail (Thin dashed line, only shown when Snap to Road is active) */}
              {useSnapping && rawTrail.length >= 2 && (
                <Polyline
                  positions={rawTrail}
                  pathOptions={{
                    color:   color,
                    weight:  isSelected ? 2.5 : 1.5,
                    opacity: isSelected ? 0.4 : 0.25,
                    dashArray: "5 5",
                    lineJoin: "round",
                    lineCap:  "round",
                  }}
                />
              )}

              {/* Snapped/Active Trail (Solid line) */}
              {trail.length >= 2 && (
                <Polyline
                  positions={trail}
                  pathOptions={{
                    color:   color,
                    weight:  isSelected ? 5 : 3,
                    opacity: isSelected ? 0.9 : 0.6,
                    lineJoin: "round",
                    lineCap:  "round",
                  }}
                />
              )}
            </Fragment>
          );
        })}

        {/* ── LIVE TRUCK DISTANCE ROUTE (Neighbor Connections) ─────────────── */}
        {frontBackRoutes && frontBackRoutes.map((route, i) => (
          <Fragment key={`dist-route-${i}`}>
            <Polyline
              positions={route.geometry}
              pathOptions={{
                color: "#facc15", // Yellow
                weight: 8,
                opacity: 0.3,
                lineJoin: "round",
                lineCap: "round",
              }}
            />
            <Polyline
              positions={route.geometry}
              pathOptions={{
                color: "#eab308", // Darker yellow
                weight: 4,
                opacity: 0.9,
                lineJoin: "round",
                lineCap: "round",
                dashArray: "8 8" // dashed line for neighbor distance
              }}
            />
            <Marker
              position={[
                (route.geometry[0][0] + route.geometry[route.geometry.length - 1][0]) / 2,
                (route.geometry[0][1] + route.geometry[route.geometry.length - 1][1]) / 2
              ]}
              icon={L.divIcon({ className: "hidden" })}
              zIndexOffset={500}
            >
              <Tooltip permanent direction="center" className="bg-gray-900/90 text-yellow-400 border border-yellow-500/50 font-black text-xs px-2 py-1 rounded shadow-lg backdrop-blur-sm">
                {route.label} ({Math.round(route.distanceMeters)}m)
              </Tooltip>
            </Marker>
          </Fragment>
        ))}

        {/* ── Recording trail overlay ─────────────────────────────────────── */}
        {recording && recPoints.length >= 2 && (
          <Polyline
            positions={recPoints.map((p) => [p.lat, p.lng])}
            pathOptions={{
              color: "#EA4335",
              weight: 4,
              opacity: 0.95,
              dashArray: "8 4",
              lineJoin: "round",
            }}
          />
        )}

        {/* ── Accuracy circles + Markers ─────────────────────────────────── */}
        {vehicleIds.map((vehicleId) => {
          const data = vehicles[vehicleId];
          if (!data || typeof data.lat !== "number" || typeof data.lng !== "number")
            return null;
          const isSelected  = selectedId === vehicleId;
          const isRecording = recording && recVehicleId === vehicleId;
          const isEstimated = data.is_estimated === true && data.online;
          const color =
            isRecording ? "#EA4335" :
            isSelected  ? "#EA4335" :
            !data.online ? "#9e9e9e" :
            isEstimated ? "#f97316" :
            "#22a84b";

          return (
            <Fragment key={vehicleId}>
              {/* Raw GPS location dot and drift line (visible when selected) */}
              {isSelected && data.rawLat && data.rawLng && (
                <>
                  <Circle
                    center={[data.rawLat, data.rawLng]}
                    radius={3}
                    pathOptions={{
                      color: "#6b7280",
                      fillColor: "#6b7280",
                      fillOpacity: 0.5,
                      weight: 1,
                    }}
                  />
                  <Polyline
                    positions={[[data.rawLat, data.rawLng], [data.lat, data.lng]]}
                    pathOptions={{
                      color: "#6b7280",
                      weight: 1.5,
                      dashArray: "4 4",
                      opacity: 0.6,
                    }}
                  />
                </>
              )}

              {/* GPS accuracy ring — radius based on HDOP */}
              <AnimatedCircle
                key={`acc-${vehicleId}`}
                position={[data.lat, data.lng]}
                radius={(data.hdop && data.hdop < 99.0) ? (data.hdop * 3.5) : 10.0}
                duration={2000}
                pathOptions={{
                  color:       color,
                  fillColor:   color,
                  fillOpacity: 0.10,
                  weight:      1.5,
                  opacity:     0.55,
                  dashArray:   "4 3",
                }}
              />

              <AnimatedMarker
                key={vehicleId}
                position={[data.lat, data.lng]}
                icon={makePin(color, isSelected || isRecording, useCompass ? data.bearing : 0, isEstimated, vehicleId)}
                duration={2000}
                zIndexOffset={isSelected ? 1000 : 0}
                eventHandlers={{ click: () => onVehicleSelect(vehicleId) }}
              >
                <Popup closeButton autoPan>
                  <VehiclePopup vehicleId={vehicleId} data={data} />
                </Popup>
              </AnimatedMarker>
            </Fragment>
          );
        })}

      </MapContainer>

      {/* ── Procession Analytics HUD (top center) ── */}
      {!isAndroidAuto && processionAnalytics && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-[1000] flex items-center gap-4 md:gap-6
          bg-gray-950/90 backdrop-blur-xl border border-white/10 px-5 py-3 rounded-2xl shadow-2xl pointer-events-auto select-none max-w-[95%] md:max-w-2xl transition-all duration-300">
          
          <div className="flex items-center gap-2 shrink-0">
            <div className="w-2 h-2 rounded-full bg-orange-500 animate-pulse shadow-[0_0_8px_rgba(249,115,22,0.8)]" />
            <div className="flex flex-col justify-center">
              <span className="text-[9px] font-bold text-white/50 uppercase tracking-wider">Procession Spread</span>
              <span 
                className="text-sm font-extrabold text-orange-400 font-mono tracking-wide" 
                style={{ textShadow: "0 0 8px rgba(249, 115, 22, 0.4)" }}
              >
                {processionAnalytics.spread}
              </span>
            </div>
          </div>

          <div className="h-7 w-px bg-white/10 shrink-0" />

          <div className="flex items-center gap-2 shrink-0">
            <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse shadow-[0_0_8px_rgba(34,197,94,0.8)]" />
            <div className="flex flex-col justify-center">
              <span className="text-[9px] font-bold text-white/50 uppercase tracking-wider">Avg Speed</span>
              <span 
                className="text-sm font-extrabold text-green-400 font-mono tracking-wide" 
                style={{ textShadow: "0 0 8px rgba(34, 197, 94, 0.4)" }}
              >
                {processionAnalytics.avgSpeed}
              </span>
            </div>
          </div>

          <div className="h-7 w-px bg-white/10 shrink-0" />

          <div className="flex flex-col justify-center min-w-[120px] md:min-w-[160px] max-w-[130px] md:max-w-[180px]">
            <span className="text-[9px] font-bold text-white/50 uppercase tracking-wider">Next Landmark (ETA)</span>
            <span 
              className="text-xs font-extrabold text-blue-400 truncate" 
              style={{ textShadow: "0 0 8px rgba(59, 130, 246, 0.4)" }}
              title={processionAnalytics.nextLandmark}
            >
              {processionAnalytics.nextLandmark}
            </span>
          </div>

        </div>
      )}

      {/* ── LIVE badge (top-left) ── */}
      {!isAndroidAuto && (
        <div className="absolute top-3 left-3 z-[1000] flex items-center gap-2
          px-3 py-1.5 bg-gray-950/90 backdrop-blur-md border border-white/10
          rounded-full text-xs font-bold text-white pointer-events-none select-none shadow-md">
          <span className="w-2 h-2 bg-green-400 rounded-full animate-pulse shadow-[0_0_6px_#22c55e]" />
          Live GPS Tracking
        </div>
      )}

      {/* ── GPS Inspector floating panel ── */}
      {!isAndroidAuto && inspectorResult && (
        <div className="absolute top-3 right-16 z-[1200] min-w-[280px]
          bg-gray-950/95 backdrop-blur-xl border border-white/10 rounded-2xl
          p-4 text-white shadow-2xl pointer-events-auto select-none transition-all duration-300">
          
          <div className="flex justify-between items-center mb-3 border-b border-white/5 pb-2">
            <span className="font-extrabold text-xs text-orange-400 tracking-wider flex items-center gap-1.5">
              <span>📍</span> GPS Inspector
            </span>
            <button
              onClick={() => setInspectorResult(null)}
              className="text-white/40 hover:text-white cursor-pointer transition-colors text-sm"
            >✕</button>
          </div>

          {/* Raw click coordinates */}
          <div className="mb-3">
            <p className="text-[9px] text-white/40 font-bold uppercase tracking-wider mb-1">🖱️ Clicked Point (Raw)</p>
            <div className="flex gap-1.5 items-center">
              <code className="flex-1 bg-white/5 border border-white/5 px-2.5 py-1 rounded-lg text-[10px] font-mono text-cyan-400 truncate">
                {inspectorResult.lat.toFixed(7)}, {inspectorResult.lng.toFixed(7)}
              </code>
              <button
                onClick={() => copyToClipboard(`${inspectorResult.lat.toFixed(7)}, ${inspectorResult.lng.toFixed(7)}`, "raw")}
                className={`px-2.5 py-1 text-[9px] font-black rounded-lg cursor-pointer transition-all duration-200 border
                  ${copiedField === "raw" 
                    ? "bg-green-500/20 border-green-500/30 text-green-400 shadow-[0_0_8px_rgba(34,197,94,0.2)]" 
                    : "bg-white/5 border-white/10 hover:bg-white/10 text-white/80"}`}
              >
                {copiedField === "raw" ? "✓ Copied" : "Copy"}
              </button>
            </div>
          </div>

          {/* OSRM road-snapped coordinates */}
          <div>
            <p className="text-[9px] text-white/40 font-bold uppercase tracking-wider mb-1">🛣️ Nearest Road (OSRM)</p>
            {inspectorResult.loading ? (
              <div className="px-2.5 py-1 text-[10px] text-white/30 font-medium">⏳ Querying OSRM...</div>
            ) : inspectorResult.snapped ? (
              <div className="space-y-1.5">
                <div className="flex gap-1.5 items-center">
                  <code className="flex-1 bg-white/5 border border-white/5 px-2.5 py-1 rounded-lg text-[10px] font-mono text-emerald-400 truncate">
                    {inspectorResult.snapped.lat.toFixed(7)}, {inspectorResult.snapped.lng.toFixed(7)}
                  </code>
                  <button
                    onClick={() => copyToClipboard(`${inspectorResult.snapped.lat.toFixed(7)}, ${inspectorResult.snapped.lng.toFixed(7)}`, "snapped")}
                    className={`px-2.5 py-1 text-[9px] font-black rounded-lg cursor-pointer transition-all duration-200 border
                      ${copiedField === "snapped" 
                        ? "bg-green-500/20 border-green-500/30 text-green-400 shadow-[0_0_8px_rgba(34,197,94,0.2)]" 
                        : "bg-white/5 border-white/10 hover:bg-white/10 text-white/80"}`}
                  >
                    {copiedField === "snapped" ? "✓ Copied" : "Copy"}
                  </button>
                </div>
                <p className="text-[9px] text-white/45 font-semibold leading-normal truncate">
                  📛 {inspectorResult.snapped.name} &middot; {inspectorResult.snapped.distance.toFixed(1)}m away
                </p>
              </div>
            ) : (
              <div className="px-2.5 py-1 text-[10px] text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg font-medium">
                ⚠️ No road found nearby
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Settings & Recording Dock (top-left) ── */}
      {!isAndroidAuto && (
        <div className="absolute top-12 left-3 z-[1000] flex flex-col gap-3 p-4
          bg-gray-950/90 backdrop-blur-xl border border-white/10 rounded-2xl shadow-2xl pointer-events-auto select-none min-w-[210px] transition-all duration-300">
          
          <div className="border-b border-white/10 pb-2 flex items-center justify-between">
            <h3 className="text-xs font-extrabold text-white uppercase tracking-wider">Dashboard Settings</h3>
          </div>

          <div className="flex flex-col gap-2.5">
            {/* Snap-to-Route Toggle Switch */}
            <label htmlFor="snapToggle" className="flex items-center justify-between text-xs font-bold text-white/85 cursor-pointer select-none w-full">
              <span>Snap to Road</span>
              <div className="relative inline-flex items-center">
                <input
                  type="checkbox"
                  id="snapToggle"
                  checked={useSnapping}
                  onChange={(e) => onToggleSnapping(e.target.checked)}
                  className="sr-only peer"
                />
                <div className="w-8 h-4.5 bg-white/10 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-3.5 after:w-3.5 after:transition-all peer-checked:bg-orange-500" />
              </div>
            </label>

            {/* Compass Toggle Switch */}
            <label htmlFor="compassToggle" className="flex items-center justify-between text-xs font-bold text-white/85 cursor-pointer select-none w-full">
              <span>Enable Compass</span>
              <div className="relative inline-flex items-center">
                <input
                  type="checkbox"
                  id="compassToggle"
                  checked={useCompass}
                  onChange={(e) => onToggleCompass(e.target.checked)}
                  className="sr-only peer"
                />
                <div className="w-8 h-4.5 bg-white/10 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-3.5 after:w-3.5 after:transition-all peer-checked:bg-orange-500" />
              </div>
            </label>

            {/* Route Assigning Toggle Switch */}
            <label htmlFor="routeAssignToggle" className="flex items-center justify-between text-xs font-bold text-white/85 cursor-pointer select-none w-full border-t border-white/5 pt-2 mt-1">
              <span className="flex items-center gap-1">🗺️ Route Assigning</span>
              <div className="relative inline-flex items-center">
                <input
                  type="checkbox"
                  id="routeAssignToggle"
                  checked={assignMode}
                  onChange={(e) => {
                    setAssignMode(e.target.checked);
                    if (!e.target.checked) {
                      setAssignPoints([]);
                      setFetchedRoute([]);
                      setRouteName("");
                      setAssignStatus("");
                    }
                  }}
                  className="sr-only peer"
                />
                <div className="w-8 h-4.5 bg-white/10 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-3.5 after:w-3.5 after:transition-all peer-checked:bg-orange-500" />
              </div>
            </label>
          </div>

          {assignMode && (
            <div className="border-t border-white/10 pt-3 mt-1 flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-extrabold text-orange-400 uppercase tracking-wider">Assign Points</span>
                {assignPoints.length > 0 && (
                  <button
                    onClick={() => {
                      setAssignPoints([]);
                      setFetchedRoute([]);
                      setAssignStatus("");
                    }}
                    className="text-[9px] text-red-400 hover:text-red-300 font-bold uppercase transition"
                  >
                    Reset
                  </button>
                )}
              </div>

              <div className="flex flex-col gap-1.5">
                <div className="text-[10px] text-white/60 leading-relaxed bg-white/5 rounded-lg p-2 border border-white/5">
                  {assignPoints.length === 0 ? (
                    <span>👉 Click map to set <strong className="text-green-400">Start point</strong></span>
                  ) : assignPoints.length === 1 ? (
                    <span>👉 Click map to set <strong className="text-red-400">End point</strong></span>
                  ) : (
                    <span>✅ Snap line calculated!</span>
                  )}
                </div>

                {assignPoints.length >= 2 && (
                  <div className="flex flex-col gap-2 mt-1">
                    <input
                      type="text"
                      placeholder="Route Name (e.g. Route A)"
                      value={routeName}
                      onChange={(e) => setRouteName(e.target.value)}
                      className="bg-gray-900 border border-white/10 rounded-lg px-2.5 py-1 text-xs font-bold text-white focus:outline-none focus:border-orange-500 w-full"
                    />
                    <button
                      onClick={handleSaveRoute}
                      disabled={fetchedRoute.length === 0}
                      className="w-full py-1.5 bg-orange-500 hover:bg-orange-600 disabled:bg-white/10 disabled:text-white/30 text-white text-xs font-extrabold rounded-lg shadow-lg active:scale-[0.98] transition uppercase tracking-wider"
                    >
                      Save to MongoDB
                    </button>
                  </div>
                )}

                {assignStatus && (
                  <div className="text-[9px] font-semibold text-white/50 text-center animate-pulse mt-0.5">
                    {assignStatus}
                  </div>
                )}
              </div>
            </div>
          )}


          <div className="border-t border-white/10 pt-3 flex flex-col gap-2">
            {/* Recording status pill */}
            {recording && (
              <div className="flex items-center gap-2 px-2.5 py-1.5
                bg-red-500/10 border border-red-500/30
                rounded-xl text-[10px] font-black text-red-400 animate-pulse">
                <span className="w-1.5 h-1.5 bg-red-500 rounded-full" />
                REC: {fmt(recSeconds)} ({recPoints.length} pts)
              </div>
            )}

            {/* Saved confirmation / download buttons */}
            {recSaved && !recording && (
              <div className="flex flex-col gap-1.5 p-2 bg-green-500/10 border border-green-500/20 rounded-xl">
                <span className="text-green-400 font-bold text-[9px] uppercase tracking-wider flex items-center gap-1">
                  ✓ Saved!
                </span>
                <div className="flex gap-1.5">
                  <button
                    onClick={() => {
                      const sid = recSessionId.current || new Date().toISOString().replace(/[:.]/g, "-");
                      downloadGPX(recPoints, recVehicleId || activeVehicle, sid);
                    }}
                    className="flex-1 py-1 bg-orange-500/20 hover:bg-orange-500/30 text-orange-400 font-black text-[9px] rounded-lg transition-all border border-orange-500/30 cursor-pointer"
                  >
                    GPX
                  </button>
                  <button
                    onClick={() => {
                      const sid = recSessionId.current || new Date().toISOString().replace(/[:.]/g, "-");
                      downloadKML(recPoints, recVehicleId || activeVehicle, sid);
                    }}
                    className="flex-1 py-1 bg-blue-500/20 hover:bg-blue-500/30 text-blue-400 font-black text-[9px] rounded-lg transition-all border border-blue-500/30 cursor-pointer"
                  >
                    KML
                  </button>
                </div>
              </div>
            )}

            {/* Start / Stop button */}
            {!recording ? (
              <button
                onClick={() => activeVehicle && startRecording(activeVehicle)}
                disabled={!activeVehicle}
                className="w-full flex items-center justify-center gap-2 py-2 px-3
                  bg-white/5 border border-white/10
                  hover:bg-red-500/10 hover:border-red-500/30 hover:text-red-400
                  text-white text-xs font-bold rounded-xl
                  transition-all duration-200 cursor-pointer
                  disabled:opacity-40 disabled:cursor-not-allowed active:scale-[0.98]"
              >
                <span className="w-2 h-2 bg-red-500 rounded-full" />
                Record {activeVehicle ? `(${activeVehicle})` : "Route"}
              </button>
            ) : (
              <button
                onClick={stopRecording}
                className="w-full flex items-center justify-center gap-2 py-2 px-3
                  bg-red-500 hover:bg-red-600 active:scale-[0.98]
                  text-white text-xs font-bold rounded-xl
                  transition-all duration-200 cursor-pointer
                  border border-red-600 shadow-lg shadow-red-500/25"
              >
                <span className="w-2 h-2 bg-white rounded-sm" />
                Stop & Save
              </button>
            )}
          </div>
        </div>
      )}

      {/* ── CENTER button ── */}
      {vehicleIds.length > 0 && !isAndroidAuto && (
        <div className="absolute bottom-20 left-3 z-[1000] flex items-center gap-2
          bg-gray-950/90 backdrop-blur-md border border-white/10 p-1.5 rounded-xl shadow-lg pointer-events-auto select-none">
          <button
            onClick={() => {
              const target = selectedId || vehicleIds[0];
              setCenterTarget({ id: target, t: Date.now(), zoom: selectedZoom });
            }}
            className="flex items-center gap-1.5 px-3 py-2
              bg-orange-500 hover:bg-orange-600 active:scale-[0.98]
              text-white text-xs font-bold rounded-lg
              transition-all duration-200 cursor-pointer"
          >
            🎯 Center
          </button>
        </div>
      )}

      {/* ── Trail legend (bottom-left) ── */}
      {!isAndroidAuto && vehicleIds.some((id) => (vehicles[id]?.trail?.length || 0) >= 2) && (
        <div className="absolute bottom-8 left-3 z-[1000]
          bg-gray-950/90 backdrop-blur-md border border-white/10
          rounded-xl px-3 py-2 text-xs text-white/80 space-y-1
          pointer-events-none select-none shadow-md">
          <p className="text-white/40 font-semibold uppercase tracking-widest
            text-[9px] mb-1">Route Trail</p>
          {vehicleIds.map((id, idx) => {
            const trail = vehicles[id]?.trail || [];
            if (trail.length < 2) return null;
            return (
              <div key={id} className="flex items-center gap-2">
                <span
                  style={{ background: TRAIL_COLORS[idx % TRAIL_COLORS.length] }}
                  className="w-4 h-1.5 rounded-full inline-block flex-shrink-0"
                />
                <span className="font-medium">{id}</span>
                <span className="text-white/40 font-mono text-[10px]">({trail.length} pts)</span>
              </div>
            );
          })}
        </div>
      )}

      {/* ── PLAYBACK Control Panel ── */}
      {!isAndroidAuto && (
        <div className="absolute bottom-6 left-1/2 -translate-x-1/2 z-[1000] flex flex-col gap-3.5
          bg-gray-950/95 backdrop-blur-xl border border-white/10 p-4 rounded-2xl shadow-2xl pointer-events-auto select-none min-w-[340px] md:min-w-[500px] transition-all duration-300">
          
          {/* Header Row */}
          <div className="flex items-center justify-between w-full border-b border-white/10 pb-2.5">
            <div className="flex items-center gap-2">
              <span className="text-base animate-pulse">🎬</span>
              <span className="font-extrabold text-xs text-white uppercase tracking-wider">Route Replay Player</span>
            </div>
            <button
              onClick={() => onTogglePlaybackMode(!playbackMode)}
              className={`px-3 py-1.5 text-[10px] font-black rounded-lg cursor-pointer transition-all duration-200 border uppercase tracking-wider
                ${playbackMode 
                  ? "bg-red-500/20 text-red-400 border-red-500/30 hover:bg-red-500/30 active:scale-[0.97]" 
                  : "bg-orange-500/20 text-orange-400 border-orange-500/30 hover:bg-orange-500/30 active:scale-[0.97]"}`}
            >
              {playbackMode ? "Exit Playback" : "Start Playback"}
            </button>
          </div>

          {/* Main Controls Panel (only visible when in playback mode) */}
          {playbackMode && (
            <div className="w-full flex flex-col gap-4 animate-slide-in">
              
              {/* Row 1: Recording Selector & Device ID Info */}
              <div className="flex flex-col gap-3 bg-white/3 border border-white/5 rounded-xl p-3">
                <div className="flex flex-wrap md:flex-nowrap items-end gap-3 w-full">
                  <div className="flex flex-col gap-1 flex-1 min-w-0 w-full">
                    <label className="text-[9px] text-white/40 font-bold uppercase tracking-wider">Select Vehicle</label>
                    <select
                      value={playbackVehicleId}
                      onChange={(e) => onPlaybackVehicleIdChange(e.target.value)}
                      className="bg-gray-900 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs font-bold text-white focus:outline-none focus:border-orange-500 cursor-pointer w-full transition-all"
                    >
                      <option value="">-- Choose Truck --</option>
                      {(() => {
                        const list = (Array.isArray(deviceList) && deviceList.length > 0) ? deviceList : Object.keys(vehicleLabels);
                        return [...list].sort((a, b) => {
                          const numA = parseInt(a.replace(/\D/g, ""), 10);
                          const numB = parseInt(b.replace(/\D/g, ""), 10);
                          if (!isNaN(numA) && !isNaN(numB)) return numA - numB;
                          return a.localeCompare(b);
                        }).map(vid => (
                          <option key={vid} value={vid}>{vehicles[vid]?.display_name || vehicleLabels[vid] || vid}</option>
                        ));
                      })()}
                    </select>
                  </div>

                  <div className="flex flex-col gap-1 shrink-0 w-full md:w-36">
                    <label className="text-[9px] text-white/40 font-bold uppercase tracking-wider">Select Date</label>
                    <input
                      type="date"
                      value={playbackDate}
                      onChange={(e) => onPlaybackDateChange(e.target.value)}
                      className="bg-gray-900 border border-white/10 rounded-lg px-2.5 py-1 text-xs font-bold text-white focus:outline-none focus:border-orange-500 cursor-pointer w-full transition-all"
                    />
                  </div>

                  <div className="flex flex-col gap-1 shrink-0 w-full md:w-24">
                    <label className="text-[9px] text-white/40 font-bold uppercase tracking-wider">Start Time</label>
                    <input
                      type="time"
                      value={playbackStartTime}
                      onChange={(e) => onPlaybackStartTimeChange(e.target.value)}
                      className="bg-gray-900 border border-white/10 rounded-lg px-2.5 py-1 text-xs font-bold text-white focus:outline-none focus:border-orange-500 cursor-pointer w-full transition-all"
                    />
                  </div>

                  <div className="flex flex-col gap-1 shrink-0 w-full md:w-24">
                    <label className="text-[9px] text-white/40 font-bold uppercase tracking-wider">End Time</label>
                    <input
                      type="time"
                      value={playbackEndTime}
                      onChange={(e) => onPlaybackEndTimeChange(e.target.value)}
                      className="bg-gray-900 border border-white/10 rounded-lg px-2.5 py-1 text-xs font-bold text-white focus:outline-none focus:border-orange-500 cursor-pointer w-full transition-all"
                    />
                  </div>

                  <button
                    onClick={() => onLoadPlayback(playbackVehicleId, playbackDate, playbackStartTime, playbackEndTime)}
                    disabled={loadingPlayback}
                    className="px-4 py-2 text-xs font-extrabold rounded-lg bg-orange-500 hover:bg-orange-600 text-white disabled:opacity-50 cursor-pointer shrink-0 transition-all uppercase tracking-wider w-full md:w-auto shadow-md"
                  >
                    {loadingPlayback ? "Loading..." : "Load"}
                  </button>
                </div>

                <div className="flex items-center justify-between border-t border-white/10 pt-2 w-full">
                  <span className="text-[9px] text-white/40 font-bold uppercase tracking-wider">Active Device: <span className="text-orange-400 font-extrabold">{selectedRecordVehicleId || "None"}</span></span>
                  {playbackRoutePoints.length > 0 && (
                    <span className="text-[9px] text-white/40 font-bold uppercase tracking-wider">
                      Points Loaded: <span className="text-white font-extrabold">{playbackRoutePoints.length}</span>
                      {totalRawPoints > playbackRoutePoints.length && (
                        <span className="text-white/40"> (Total: {totalRawPoints})</span>
                      )}
                    </span>
                  )}
                </div>
              </div>

              {/* Row 2: Timeline Slider & Points Progress (Full Width) */}
              <div className="flex flex-col gap-1.5">
                <input
                  type="range"
                  min="0"
                  max={playbackRoutePoints.length > 0 ? playbackRoutePoints.length - 1 : 0}
                  value={playbackIndex}
                  onChange={(e) => onPlaybackIndexChange(Number(e.target.value))}
                  className="w-full h-1.5 rounded-lg bg-white/10 accent-orange-500 cursor-pointer outline-none hover:bg-white/20 transition-all"
                />
                <div className="flex justify-between text-[9px] text-white/40 font-bold uppercase tracking-wider">
                  <span>Start</span>
                  <span className="text-white/80 font-bold font-mono">
                    Point {playbackIndex + 1} / {playbackRoutePoints.length} ({Math.round((playbackRoutePoints.length > 1 ? playbackIndex / (playbackRoutePoints.length - 1) : 0) * 100)}%)
                  </span>
                  <span>End</span>
                </div>
              </div>

              {/* Row 3: Centered Controls & Speed Selector */}
              <div className="flex items-center justify-between border-t border-white/5 pt-3.5 relative">
                <div className="flex items-center gap-1">
                  <span className={`w-2 h-2 rounded-full ${playbackIsPlaying ? "bg-green-400 animate-pulse" : "bg-gray-500"}`} />
                  <span className="text-[9px] text-white/40 font-bold uppercase tracking-wider">{playbackIsPlaying ? "Playing" : "Paused"}</span>
                </div>

                {/* Centered Play/Pause Button */}
                <div className="absolute left-1/2 -translate-x-1/2 flex items-center">
                  <button
                    onClick={() => onPlaybackIsPlayingChange(!playbackIsPlaying)}
                    className="w-12 h-12 flex items-center justify-center rounded-full bg-gradient-to-r from-orange-500 to-red-500 hover:from-orange-400 hover:to-red-400 active:scale-95 text-white text-lg transition-all duration-200 cursor-pointer shadow-lg shadow-orange-500/25 hover:shadow-orange-500/45 border border-white/10"
                  >
                    {playbackIsPlaying ? "⏸" : "▶"}
                  </button>
                </div>

                {/* Speed buttons on the right */}
                <div className="flex items-center gap-0.5 bg-white/5 border border-white/10 rounded-xl p-0.5 shadow-inner">
                  {[1, 2, 5, 10].map((speed) => (
                    <button
                      key={speed}
                      onClick={() => onPlaybackSpeedChange(speed)}
                      className={`px-2.5 py-1 text-[9px] font-black rounded-lg cursor-pointer transition-all duration-150
                        ${playbackSpeed === speed 
                          ? "bg-orange-500 text-white shadow-sm" 
                          : "text-white/60 hover:text-white hover:bg-white/5"}`}
                    >
                      {speed}x
                    </button>
                  ))}
                </div>
              </div>

            </div>
          )}
        </div>
      )}
    </div>
  );
}
