import { useState, useEffect, useRef } from "react";

import { DISPLAY_TO_DEVICE_MAP, LANDMARKS } from "../../lib/constants";
import { haversine } from "../../utils/routeSnap";
import { fmtCoord, fmtSpeed, formatDateTime } from "../../utils/formatters";

// Self-contained HLS Player component to stream RTMP via MediaMTX transcoded HLS
function HlsPlayer({ url }) {
  const videoRef = useRef(null);

  useEffect(() => {
    let hls = null;

    const initPlayer = () => {
      const video = videoRef.current;
      if (!video) return;

      if (video.canPlayType("application/vnd.apple.mpegurl")) {
        // Native HLS support (Safari, iOS)
        video.src = url;
      } else if (window.Hls) {
        // Hls.js support
        hls = new window.Hls();
        hls.loadSource(url);
        hls.attachMedia(video);
      }
    };

    if (!window.Hls) {
      const script = document.createElement("script");
      script.src = "https://cdn.jsdelivr.net/npm/hls.js@latest";
      script.async = true;
      script.onload = initPlayer;
      document.body.appendChild(script);
      return () => {
        if (script.parentNode) {
          script.parentNode.removeChild(script);
        }
        if (hls) {
          hls.destroy();
        }
      };
    } else {
      initPlayer();
      return () => {
        if (hls) {
          hls.destroy();
        }
      };
    }
  }, [url]);

  return (
    <video
      ref={videoRef}
      controls
      autoPlay
      muted
      playsInline
      className="w-full h-auto rounded-xl border border-white/10 bg-black aspect-video shadow-md focus:outline-none"
    />
  );
}

function getCardinalDirection(deg) {
  const d = (deg % 360 + 360) % 360;
  if (d >= 337.5 || d < 22.5) return "N (North)";
  if (d >= 22.5 && d < 67.5) return "NE (North-East)";
  if (d >= 67.5 && d < 112.5) return "E (East)";
  if (d >= 112.5 && d < 157.5) return "SE (South-East)";
  if (d >= 157.5 && d < 202.5) return "S (South)";
  if (d >= 202.5 && d < 247.5) return "SW (South-West)";
  if (d >= 247.5 && d < 292.5) return "W (West)";
  return "NW (North-West)";
}

function buildNavUrl(lat, lng) {
  return `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lng}&zoom=16`;
}

function buildOsmTileUrl(lat, lng) {
  const zoom = 15;
  const x = Math.floor(((lng + 180) / 360) * Math.pow(2, zoom));
  const latRad = (lat * Math.PI) / 180;
  const y = Math.floor(
    ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) *
    Math.pow(2, zoom)
  );
  return `https://tile.openstreetmap.org/${zoom}/${x}/${y}.png`;
}

function StatRow({ label, value, mono = false }) {
  return (
    <div className="flex items-center justify-between py-3 border-b border-white/8">
      <span className="text-xs text-white/50 uppercase tracking-wider font-medium">
        {label}
      </span>
      <span className={`text-sm text-white font-semibold ${mono ? "font-mono" : ""}`}>
        {value ?? "—"}
      </span>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-4 px-6 text-center">
      <div className="w-16 h-16 rounded-2xl bg-white/5 flex items-center justify-center text-3xl">
        🗺️
      </div>
      <div>
        <p className="text-white/60 font-medium text-sm">No vehicle selected</p>
        <p className="text-white/30 text-xs mt-1">
          Click a vehicle in the sidebar or tap a map marker
        </p>
      </div>
    </div>
  );
}

export function DetailPanel({ vehicleId, data, useCompass, onToggleCompass, adminMode = false, logs = {}, onSelectVehicle, globalConfig }) {
  const [activeTab, setActiveTab] = useState("details"); // "details" or "logs"
  const [calibrating, setCalibrating] = useState(false);
  const [calibrated, setCalibrated] = useState(false);
  const [alignmentAngle, setAlignmentAngle] = useState("auto"); // "auto", "0", "90", "180", "270", "custom"
  const [customAngle, setCustomAngle] = useState(0);

  const [editName, setEditName] = useState("");
  const [savingName, setSavingName] = useState(false);
  const [isEditingName, setIsEditingName] = useState(false);

  useEffect(() => {
    if (data) {
      setEditName(data.display_name || vehicleId);
    }
    setIsEditingName(false);
  }, [vehicleId, data?.display_name]);

  const handleSaveName = async () => {
    setSavingName(true);
    try {
      const cleanName = editName.trim() || null;
      const res = await fetch("/api/save_vehicle_display_name", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_id: vehicleId, display_name: cleanName })
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      setIsEditingName(false);
    } catch (e) {
      console.error("Failed to save display name:", e);
    } finally {
      setSavingName(false);
    }
  };

  // Live sensor & filter config tuning state
  const [configScope, setConfigScope] = useState("vehicle"); // "vehicle" or "global"
  const [kalmanQ, setKalmanQ] = useState(0.000005);
  const [kalmanR, setKalmanR] = useState(0.00005);
  const [motionThresh, setMotionThresh] = useState(800);
  const [lockRadius, setLockRadius] = useState(15);
  const [savingConfig, setSavingConfig] = useState(false);
  const [configSaved, setConfigSaved] = useState(false);
  const [resettingConfig, setResettingConfig] = useState(false);
  const [configReset, setConfigReset] = useState(false);

  // Sync form values when vehicle selection, config scope, global config or local config changes
  useEffect(() => {
    if (configScope === "global") {
      setKalmanQ(globalConfig?.kalman_q ?? 0.000005);
      setKalmanR(globalConfig?.kalman_r ?? 0.00005);
      setMotionThresh(globalConfig?.motion_threshold ?? 800);
      setLockRadius(globalConfig?.stationary_lock_radius ?? 15);
    } else if (data) {
      setKalmanQ(data.config?.kalman_q ?? 0.000005);
      setKalmanR(data.config?.kalman_r ?? 0.00005);
      setMotionThresh(data.config?.motion_threshold ?? 800);
      setLockRadius(data.config?.stationary_lock_radius ?? 15);
    }
  }, [configScope, globalConfig, vehicleId, data?.config]);

  const handleSaveConfig = async () => {
    setSavingConfig(true);
    setConfigSaved(false);
    try {
      const payload = {
        kalman_q: Number(kalmanQ),
        kalman_r: Number(kalmanR),
        motion_threshold: Number(motionThresh),
        stationary_lock_radius: Number(lockRadius),
      };

      if (configScope === "global") {
        const res = await fetch("/api/save_global_config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ config: payload })
        });
        if (!res.ok) throw new Error("HTTP " + res.status);
      } else {
        const targetId = DISPLAY_TO_DEVICE_MAP[vehicleId] || vehicleId;
        const res = await fetch("/api/save_config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ vehicle_id: targetId, config: payload })
        });
        if (!res.ok) throw new Error("HTTP " + res.status);
      }

      setConfigSaved(true);
      setTimeout(() => setConfigSaved(false), 3000);
    } catch (e) {
      console.error("Failed to save config:", e);
    } finally {
      setSavingConfig(false);
    }
  };

  const handleResetConfig = async () => {
    setResettingConfig(true);
    setConfigReset(false);
    try {
      const targetId = DISPLAY_TO_DEVICE_MAP[vehicleId] || vehicleId;
      const res = await fetch("/api/save_config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vehicle_id: targetId, config: null })
      });
      if (!res.ok) throw new Error("HTTP " + res.status);

      setConfigReset(true);
      setTimeout(() => setConfigReset(false), 3000);
    } catch (e) {
      console.error("Failed to reset config:", e);
    } finally {
      setResettingConfig(false);
    }
  };

  const handleCalibrate = async () => {
    setCalibrating(true);
    setCalibrated(false);
    try {
      let angle = data.bearing || 0;
      if (alignmentAngle === "custom") {
        angle = Number(customAngle);
      } else if (alignmentAngle !== "auto") {
        angle = Number(alignmentAngle);
      }

      const targetId = DISPLAY_TO_DEVICE_MAP[vehicleId] || vehicleId;
      const res = await fetch("/api/calibrate_vehicle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_id: targetId, angle: angle })
      });
      if (!res.ok) throw new Error("HTTP " + res.status);

      // Simulation delay for user visual feedback
      await new Promise((resolve) => setTimeout(resolve, 1500));
      setCalibrated(true);
      setTimeout(() => setCalibrated(false), 3000);
    } catch (e) {
      console.error("Calibration failed:", e);
    } finally {
      setCalibrating(false);
    }
  };

  return (
    <aside className="flex flex-col h-full bg-gray-900/85 backdrop-blur-xl border-l border-white/10 animate-slide-in">
      {/* Segmented Tab Bar */}
      <div className="flex border-b border-white/10 bg-black/25 shrink-0">
        <button
          onClick={() => setActiveTab("details")}
          className={`flex-1 py-3 text-xs font-bold uppercase tracking-wider transition-all duration-200 border-b-2
            ${activeTab === "details"
              ? "text-orange-500 border-orange-500 bg-white/5"
              : "text-white/40 border-transparent hover:text-white/80 hover:bg-white/2"}`}
        >
          📋 Details
        </button>
        <button
          onClick={() => setActiveTab("logs")}
          className={`flex-1 py-3 text-xs font-bold uppercase tracking-wider transition-all duration-200 border-b-2
            ${activeTab === "logs"
              ? "text-orange-500 border-orange-500 bg-white/5"
              : "text-white/40 border-transparent hover:text-white/80 hover:bg-white/2"}`}
        >
          ⚠️ Alerts Log
        </button>
      </div>

      {activeTab === "details" ? (
        !vehicleId || !data ? (
          <EmptyState />
        ) : (
          <div className="flex-1 flex flex-col min-h-0">
            {/* Header */}
            <div className="px-5 py-5 border-b border-white/10">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-orange-500 to-red-600 flex items-center justify-center text-xl shrink-0 shadow-lg shadow-orange-500/20">
                  🚌
                </div>
                <div className="flex-1 min-w-0">
                  {isEditingName ? (
                    <div className="flex items-center gap-1.5 w-full">
                      <input
                        type="text"
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        className="bg-white/5 border border-white/20 rounded-lg py-1 px-2 text-xs text-white focus:outline-none focus:border-orange-500 w-full"
                        placeholder="Display name"
                        autoFocus
                      />
                      <button
                        onClick={handleSaveName}
                        disabled={savingName}
                        className="bg-green-500 hover:bg-green-600 text-white font-bold text-[10px] px-2.5 py-1.5 rounded-lg cursor-pointer disabled:opacity-50"
                      >
                        {savingName ? "..." : "Save"}
                      </button>
                      <button
                        onClick={() => {
                          setIsEditingName(false);
                          setEditName(data?.display_name || vehicleId);
                        }}
                        className="bg-white/5 hover:bg-white/10 text-white/60 font-bold text-[10px] px-2.5 py-1.5 rounded-lg cursor-pointer"
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <h2 className="text-base font-bold text-white truncate">{data?.display_name || vehicleId}</h2>
                      {adminMode && (
                        <button
                          onClick={() => setIsEditingName(true)}
                          className="text-white/40 hover:text-white text-xs cursor-pointer select-none shrink-0"
                        >
                          ✏️ Edit
                        </button>
                      )}
                    </div>
                  )}
                  <div className="flex flex-wrap items-center gap-2 mt-0.5">
                    <div
                      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold
                      ${data.online ? "bg-green-500/20 text-green-400" : "bg-gray-500/20 text-gray-400"}`}
                    >
                      <span
                        className={`w-1.5 h-1.5 rounded-full ${data.online ? "bg-green-400 animate-pulse" : "bg-gray-400"
                          }`}
                      />
                      {data.online ? "Online" : "Offline"}
                    </div>
                    {data.is_estimated && data.online && (
                      <div className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold bg-orange-500/20 text-orange-400 animate-pulse">
                        <span>⚠️</span> IMU Estimated
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>

            {/* Warning banner if estimated */}
            {data.is_estimated && data.online && (
              <div className="mx-5 mt-4 p-3 rounded-xl bg-orange-500/10 border border-orange-500/20 text-xs text-orange-300 flex items-start gap-2.5 shrink-0">
                <span className="text-base leading-none">⚠️</span>
                <div>
                  <p className="font-bold">Dead Reckoning Active</p>
                  <p className="text-orange-300/80 mt-0.5 leading-relaxed">
                    GPS signal is weak or obstructed. Coordinates are being projected in real-time using MPU6050 Gyroscope & decayed speed sensor fusion.
                  </p>
                </div>
              </div>
            )}

            {/* Stats */}
            <div className="flex-1 overflow-y-auto px-5 py-2 scrollbar-thin space-y-4">
              {/* Live Camera Stream (Truck 2 only) */}
              {vehicleId === "TRUCK_2" && (
                <div className="p-4 rounded-2xl bg-white/5 border border-white/10 shadow-lg shadow-black/20 backdrop-blur-md space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-lg">📹</span>
                      <div>
                        <h3 className="text-xs font-bold text-white">Live Camera Feed</h3>
                        <p className="text-[10px] text-white/45 font-mono select-all">rtmp://103.250.160.75:1935/live/26_pilab</p>
                      </div>
                    </div>
                    <span className="text-[9px] text-red-400 bg-red-400/10 px-2.5 py-0.5 rounded-full font-semibold animate-pulse">
                      Live
                    </span>
                  </div>
                  <HlsPlayer url="http://103.250.160.75:8888/live/26_pilab/index.m3u8" />
                </div>
              )}

              {/* Real-time Compass & Speedometer Widget */}
              <div className="p-4 rounded-2xl bg-white/5 border border-white/10 flex items-center justify-around gap-4 shadow-lg shadow-black/20 backdrop-blur-md">
                {useCompass ? (
                  /* Circular Compass Dial */
                  <div className="relative w-24 h-24 rounded-full border-2 border-white/20 flex items-center justify-center bg-gray-950/40">
                    <span className="absolute top-1 text-[9px] font-black text-red-500">N</span>
                    <span className="absolute right-1.5 text-[9px] font-bold text-white/50">E</span>
                    <span className="absolute bottom-1 text-[9px] font-bold text-white/50">S</span>
                    <span className="absolute left-1.5 text-[9px] font-bold text-white/50">W</span>

                    <div className="absolute inset-0.5 rounded-full border border-dashed border-white/10 pointer-events-none" />

                    <div
                      className="absolute w-full h-full flex items-center justify-center transition-transform duration-500 ease-out"
                      style={{ transform: `rotate(${data.bearing || 0}deg)` }}
                    >
                      <div className="w-1.5 h-16 relative flex flex-col items-center">
                        <div className="w-0 h-0 border-l-[3px] border-l-transparent border-r-[3px] border-r-transparent border-b-[24px] border-b-red-500" />
                        <div className="w-0 h-0 border-l-[3px] border-l-transparent border-r-[3px] border-r-transparent border-t-[24px] border-t-white/60 mt-[8px]" />
                        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-2 h-2 rounded-full bg-white shadow border border-black/40" />
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="relative w-24 h-24 rounded-full border-2 border-white/5 flex flex-col items-center justify-center bg-gray-950/20 text-white/20 select-none">
                    <span className="text-xl">🧭</span>
                    <span className="text-[9px] font-bold mt-1 uppercase tracking-wider">Compass Off</span>
                  </div>
                )}

                {/* Speed & Heading details */}
                <div className="flex-1 flex flex-col justify-center">
                  <p className="text-[10px] text-white/40 uppercase tracking-widest font-semibold">Real-time Bearing</p>
                  <p className="text-xl font-bold text-white font-mono mt-0.5">
                    {useCompass ? `${(data.bearing || 0).toFixed(1)}°` : "Disabled"}
                  </p>
                  <p className="text-xs text-orange-400 font-semibold mt-0.5 flex items-center gap-1">
                    🧭 {useCompass ? getCardinalDirection(data.bearing || 0) : "Compass Off"}
                  </p>
                  <div className="h-px bg-white/10 my-2" />
                  <p className="text-[10px] text-white/40 uppercase tracking-widest font-semibold">Movement State</p>
                  <p className="text-xs text-white font-medium mt-0.5 flex items-center gap-1.5">
                    {data.is_moving ? (
                      <>
                        <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
                        Moving at {data.speed || 0} km/h
                      </>
                    ) : (
                      <>
                        <span className="w-2 h-2 rounded-full bg-gray-500" />
                        Stationary (0 km/h)
                      </>
                    )}
                  </p>
                </div>
              </div>

              {/* Route Snapping & Alerts Section */}
              {(() => {
                const hasCoords = typeof data.lat === "number" && typeof data.lng === "number";
                if (!hasCoords) return null;

                return (
                  <div className="p-4 rounded-2xl bg-white/5 border border-white/10 shadow-lg shadow-black/20 backdrop-blur-md space-y-3">
                    <div className="flex items-center justify-between border-b border-white/5 pb-2">
                      <h3 className="text-xs font-bold text-white/80">Route Status</h3>
                      <span className="text-[9px] text-white/40 uppercase tracking-widest font-bold">Alert HUD</span>
                    </div>

                    {data.offRoute ? (
                      <div className="p-3 rounded-xl bg-red-500/10 border border-red-500/25 flex items-center justify-between animate-pulse">
                        <div className="flex items-center gap-2">
                          <span className="text-base">⚠️</span>
                          <div>
                            <p className="text-[10px] text-red-400 font-black uppercase tracking-wider">Device Status</p>
                            <p className="text-xs font-extrabold text-white">Out of Range (Off Route)</p>
                          </div>
                        </div>
                        <span className="px-2 py-0.5 rounded-full text-[9px] font-black uppercase tracking-wide bg-red-500 text-gray-950">
                          ALERT
                        </span>
                      </div>
                    ) : (
                      <div className="p-3 rounded-xl bg-green-500/10 border border-green-500/25 flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span className="text-base">✅</span>
                          <div>
                            <p className="text-[10px] text-green-400 font-black uppercase tracking-wider">Device Status</p>
                            <p className="text-xs font-extrabold text-white">Within Route Range</p>
                          </div>
                        </div>
                        <span className="px-2 py-0.5 rounded-full text-[9px] font-black uppercase tracking-wide bg-green-500 text-gray-950">
                          NORMAL
                        </span>
                      </div>
                    )}

                    <div className="flex items-center justify-between pt-1 text-xs">
                      <span className="text-white/50 font-medium">Distance to Route</span>
                      <span className="font-bold text-white font-mono bg-white/5 px-2.5 py-1 rounded-lg border border-white/5">
                        📏 {Math.round(data.distanceMeters || 0)} m
                      </span>
                    </div>
                  </div>
                );
              })()}

              {/* Stats Card Grid */}
              <div className="p-4 rounded-2xl bg-white/3 border border-white/5 shadow-md space-y-1">
                <StatRow label="Vehicle ID" value={data.vehicle_id ?? vehicleId} />
                <StatRow label="Tracking Mode" value={!data.online ? "Offline" : (data.is_estimated ? "⚠️ IMU Estimated" : "🛰️ GPS Active")} />
                <StatRow label="Snapped Lat" value={fmtCoord(data.lat)} mono />
                <StatRow label="Snapped Lng" value={fmtCoord(data.lng)} mono />
                <StatRow label="Raw GPS Lat" value={fmtCoord(data.rawLat)} mono />
                <StatRow label="Raw GPS Lng" value={fmtCoord(data.rawLng)} mono />
                <StatRow label="GPS Drift (Error)" value={data.driftError !== undefined ? `${data.driftError} m` : "—"} />
                <StatRow label="Speed" value={fmtSpeed(data.speed)} />
                <StatRow label="Satellites" value={data.satellites !== undefined ? `${data.satellites} sats` : "—"} />
                <StatRow label="HDOP" value={data.hdop !== undefined ? `${Number(data.hdop).toFixed(2)}` : "—"} mono />
                <StatRow label="Last Update" value={formatDateTime(data.timestamp)} />
              </div>

              {/* Snap Debug Section */}
              <div className="p-4 bg-red-950/10 rounded-2xl border border-red-500/10 text-xs space-y-1.5">
                <p className="font-bold text-red-400">Snapping Debug Info</p>
                <p className="text-white/60">Snap Active: <span className="font-mono text-white">{String(data.offRoute !== undefined)}</span></p>
                <p className="text-white/60">Off Route: <span className="font-mono text-white">{String(data.offRoute)}</span></p>
                <p className="text-white/60">Distance to Route: <span className="font-mono text-white">{data.distanceMeters} m</span></p>
                <p className="text-white/60">Drift Error: <span className="font-mono text-white">{data.driftError} m</span></p>
              </div>

              {/* OSM Tile Preview */}
              <a
                href={buildNavUrl(data.lat, data.lng)}
                target="_blank"
                rel="noopener noreferrer"
                className="block rounded-2xl overflow-hidden border border-white/10 relative group shadow-md"
              >
                <img
                  src={buildOsmTileUrl(data.lat, data.lng)}
                  alt="Map tile"
                  className="w-full h-36 object-cover"
                  style={{ imageRendering: "crisp-edges" }}
                />
                <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
                  <span className="text-white text-xs font-semibold bg-black/60 px-3 py-1.5 rounded-full">
                    Open in OpenStreetMap →
                  </span>
                </div>
                <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                  <span className="text-2xl drop-shadow-lg" style={{ filter: "drop-shadow(0 2px 4px rgba(0,0,0,0.6))" }}>📍</span>
                </div>
              </a>

              {/* Coordinates badge */}
              <div className="px-3 py-2 bg-white/5 rounded-xl border border-white/5">
                <p className="text-[10px] text-white/40 mb-0.5 uppercase tracking-wider font-semibold">Coordinates</p>
                <p className="text-xs font-mono text-white/80">
                  {fmtCoord(data.lat)}, {fmtCoord(data.lng)}
                </p>
              </div>

              {/* Export Trail Section */}
              {data.trail && data.trail.length > 0 && (
                <div className="p-4 rounded-xl bg-white/5 border border-white/10 space-y-2.5">
                  <div>
                    <p className="text-xs text-white/80 font-bold">Export Trail Data</p>
                    <p className="text-[10px] text-white/40 mt-0.5 leading-relaxed">
                      Download the current breadcrumb trail for Google Maps or Google Earth.
                    </p>
                  </div>
                  <div className="flex gap-1.5">
                    <button
                      onClick={() => {
                        const sid = new Date().toISOString().replace(/[:.]/g, "-");
                        const normPoints = data.trail.map((p, i) => {
                          const lat = Array.isArray(p) ? p[0] : p.lat;
                          const lng = Array.isArray(p) ? p[1] : p.lng;
                          return { lat, lng, speed: 0, timestamp: new Date(Date.now() - (data.trail.length - 1 - i) * 5000).toISOString() };
                        });

                        const header = "index,latitude,longitude,speed_kmh,timestamp\n";
                        const rows = normPoints.map((p, i) => `${i + 1},${p.lat},${p.lng},0,${p.timestamp}`).join("\n");
                        const blob = new Blob([header + rows], { type: "text/csv" });
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement("a");
                        a.href = url;
                        a.download = `trail_${vehicleId}_${sid}.csv`;
                        a.click();
                        URL.revokeObjectURL(url);
                      }}
                      className="flex-1 py-1.5 bg-white/5 hover:bg-white/10 border border-white/10 text-white font-extrabold text-[10px] rounded-lg cursor-pointer transition-all text-center"
                    >
                      CSV
                    </button>
                    <button
                      onClick={() => {
                        const sid = new Date().toISOString().replace(/[:.]/g, "-");
                        const normPoints = data.trail.map((p, i) => {
                          const lat = Array.isArray(p) ? p[0] : p.lat;
                          const lng = Array.isArray(p) ? p[1] : p.lng;
                          return { lat, lng, speed: 0, timestamp: new Date(Date.now() - (data.trail.length - 1 - i) * 5000).toISOString() };
                        });

                        let gpx = `<?xml version="1.0" encoding="UTF-8"?>\n`;
                        gpx += `<gpx version="1.1" creator="Rath Yatra GPS Tracker" xmlns="http://www.topografix.com/GPX/1/1">\n`;
                        gpx += `  <metadata><name>Trail for ${vehicleId}</name><time>${new Date().toISOString()}</time></metadata>\n`;
                        gpx += `  <trk>\n`;
                        gpx += `    <name>${vehicleId} Trail</name>\n`;
                        gpx += `    <trkseg>\n`;
                        normPoints.forEach((p) => {
                          gpx += `      <trkpt lat="${p.lat}" lon="${p.lng}"><time>${p.timestamp}</time></trkpt>\n`;
                        });
                        gpx += `    </trkseg>\n`;
                        gpx += `  </trk>\n`;
                        gpx += `</gpx>`;
                        const blob = new Blob([gpx], { type: "application/gpx+xml" });
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement("a");
                        a.href = url;
                        a.download = `trail_${vehicleId}_${sid}.gpx`;
                        a.click();
                        URL.revokeObjectURL(url);
                      }}
                      className="flex-1 py-1.5 bg-orange-500/20 hover:bg-orange-500/30 border border-orange-500/30 text-orange-400 font-extrabold text-[10px] rounded-lg cursor-pointer transition-all text-center"
                    >
                      GPX
                    </button>
                    <button
                      onClick={() => {
                        const sid = new Date().toISOString().replace(/[:.]/g, "-");
                        const normPoints = data.trail.map((p, i) => {
                          const lat = Array.isArray(p) ? p[0] : p.lat;
                          const lng = Array.isArray(p) ? p[1] : p.lng;
                          return { lat, lng };
                        });

                        let kml = `<?xml version="1.0" encoding="UTF-8"?>\n`;
                        kml += `<kml xmlns="http://www.opengis.net/kml/2.2">\n`;
                        kml += `  <Document>\n`;
                        kml += `    <name>${vehicleId} Trail - ${sid}</name>\n`;
                        kml += `    <Placemark>\n`;
                        kml += `      <name>${vehicleId} Trail</name>\n`;
                        kml += `      <LineString>\n`;
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
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement("a");
                        a.href = url;
                        a.download = `trail_${vehicleId}_${sid}.kml`;
                        a.click();
                        URL.revokeObjectURL(url);
                      }}
                      className="flex-1 py-1.5 bg-blue-500/20 hover:bg-blue-500/30 border border-blue-500/30 text-blue-400 font-extrabold text-[10px] rounded-lg cursor-pointer transition-all text-center"
                    >
                      KML
                    </button>
                  </div>
                </div>
              )}

              {/* Calibration Controls */}
              <div className="p-4 rounded-xl bg-white/5 border border-white/10 space-y-3">
                <div className="flex items-center justify-between border-b border-white/5 pb-2.5">
                  <span className="text-xs text-white/80 font-bold">Compass Display</span>
                  <button
                    onClick={() => onToggleCompass(!useCompass)}
                    className={`px-3 py-1 rounded-full text-[10px] font-bold transition-all duration-200 cursor-pointer border
                      ${useCompass
                        ? "bg-green-500/20 border-green-500/30 text-green-400 hover:bg-green-500/30"
                        : "bg-white/5 border-white/10 text-white/60 hover:bg-white/10 hover:text-white"}`}
                  >
                    {useCompass ? "● Compass ON" : "○ Compass OFF"}
                  </button>
                </div>

                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-xs text-white/80 font-bold">IMU & Compass Calibration</p>
                    <p className="text-[10px] text-white/40 mt-0.5 leading-relaxed">
                      Aligns the internal gyroscope to match the current direction of physical travel.
                    </p>
                  </div>
                  {data.calibrate_pending && (
                    <span className="text-[10px] text-orange-400 bg-orange-400/10 px-2 py-0.5 rounded-full font-semibold animate-pulse">
                      Pending...
                    </span>
                  )}
                </div>

                {/* Alignment Direction Selector */}
                <div className="space-y-1.5">
                  <label className="text-[10px] text-white/40 uppercase tracking-widest font-semibold block">Alignment Direction</label>
                  <select
                    value={alignmentAngle}
                    onChange={(e) => setAlignmentAngle(e.target.value)}
                    className="w-full bg-white/5 border border-white/10 rounded-lg py-2 px-2.5 text-xs text-white focus:outline-none focus:border-orange-500 font-medium cursor-pointer"
                  >
                    <option value="auto" className="bg-gray-950 text-white">Auto (Keep Current / Auto-align on Drive)</option>
                    <option value="0" className="bg-gray-950 text-white">North (0° - facing forward along road)</option>
                    <option value="90" className="bg-gray-950 text-white">East (90°)</option>
                    <option value="180" className="bg-gray-950 text-white">South (180°)</option>
                    <option value="270" className="bg-gray-950 text-white">West (270°)</option>
                    <option value="custom" className="bg-gray-950 text-white">Custom Angle...</option>
                  </select>
                </div>

                {alignmentAngle === "custom" && (
                  <div className="space-y-1 animate-slide-in">
                    <label className="text-[10px] text-white/40 uppercase tracking-widest font-semibold block">Custom Degree (0-359°)</label>
                    <input
                      type="number"
                      min="0"
                      max="359"
                      value={customAngle}
                      onChange={(e) => setCustomAngle(Math.min(359, Math.max(0, Number(e.target.value))))}
                      className="w-full bg-white/5 border border-white/10 rounded-lg py-2 px-2.5 text-xs text-white font-mono focus:outline-none focus:border-orange-500"
                    />
                  </div>
                )}

                <button
                  onClick={handleCalibrate}
                  disabled={calibrating || !data.online}
                  className={`w-full py-2.5 px-3 rounded-lg text-xs font-bold transition-all duration-200 flex items-center justify-center gap-2 border
                    ${calibrated
                      ? "bg-green-500/20 border-green-500/30 text-green-400"
                      : !data.online
                        ? "bg-white/5 border-white/5 text-white/30 cursor-not-allowed"
                        : "bg-white/5 border-white/10 hover:bg-white/10 text-white cursor-pointer active:scale-[0.98]"}`}
                >
                  {calibrating ? (
                    <>
                      <span className="w-3.5 h-3.5 border-2 border-white/20 border-t-white rounded-full animate-spin" />
                      Aligning Sensors...
                    </>
                  ) : calibrated ? (
                    <>
                      <span>✔</span> Sensors Calibrated!
                    </>
                  ) : (
                    <>
                      <span>⚙</span> Calibrate Heading & Gyro
                    </>
                  )}
                </button>
              </div>

              {/* Sensor & Filter Configuration */}
              <div className="p-4 rounded-xl bg-white/5 border border-white/10 space-y-3 animate-slide-in">
                <div className="flex items-center justify-between border-b border-white/5 pb-2">
                  <h3 className="text-xs font-bold text-white/80">Sensor & Filter Parameters</h3>
                  <span className="text-[10px] text-white/40 font-mono">ESP8266 Live Tuning</span>
                </div>

                <div className="flex items-center justify-between gap-2 bg-white/5 p-1 rounded-lg border border-white/5">
                  <button
                    type="button"
                    onClick={() => setConfigScope("vehicle")}
                    className={`flex-1 py-1 rounded-md text-[10px] font-bold transition-all duration-150 cursor-pointer
                      ${configScope === "vehicle"
                        ? "bg-white text-gray-950 shadow-sm"
                        : "text-white/60 hover:text-white"}`}
                  >
                    This Vehicle
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfigScope("global")}
                    className={`flex-1 py-1 rounded-md text-[10px] font-bold transition-all duration-150 cursor-pointer
                      ${configScope === "global"
                        ? "bg-white text-gray-950 shadow-sm"
                        : "text-white/60 hover:text-white"}`}
                  >
                    Global Default
                  </button>
                </div>

                <div className="space-y-1">
                  <div className="flex justify-between items-center">
                    <label className="text-[10px] text-white/40 uppercase tracking-widest font-semibold block">
                      Kalman Q (Process Noise)
                    </label>
                    <span className="text-[9px] text-white/30 font-mono">Default: 5e-6</span>
                  </div>
                  <input
                    type="number"
                    step="0.000001"
                    min="0.0000001"
                    max="0.01"
                    value={kalmanQ}
                    onChange={(e) => setKalmanQ(Number(e.target.value))}
                    className="w-full bg-white/5 border border-white/10 rounded-lg py-1.5 px-2.5 text-xs text-white font-mono focus:outline-none focus:border-orange-500"
                  />
                </div>

                <div className="space-y-1">
                  <div className="flex justify-between items-center">
                    <label className="text-[10px] text-white/40 uppercase tracking-widest font-semibold block">
                      Kalman R (Meas. Noise)
                    </label>
                    <span className="text-[9px] text-white/30 font-mono">Default: 5e-5</span>
                  </div>
                  <input
                    type="number"
                    step="0.00001"
                    min="0.000001"
                    max="0.1"
                    value={kalmanR}
                    onChange={(e) => setKalmanR(Number(e.target.value))}
                    className="w-full bg-white/5 border border-white/10 rounded-lg py-1.5 px-2.5 text-xs text-white font-mono focus:outline-none focus:border-orange-500"
                  />
                </div>

                <div className="space-y-1">
                  <div className="flex justify-between items-center">
                    <label className="text-[10px] text-white/40 uppercase tracking-widest font-semibold block">
                      IMU Motion Threshold
                    </label>
                    <span className="text-[9px] text-white/30 font-mono">Default: 800</span>
                  </div>
                  <input
                    type="number"
                    step="50"
                    min="50"
                    max="5000"
                    value={motionThresh}
                    onChange={(e) => setMotionThresh(Number(e.target.value))}
                    className="w-full bg-white/5 border border-white/10 rounded-lg py-1.5 px-2.5 text-xs text-white font-mono focus:outline-none focus:border-orange-500"
                  />
                </div>

                <div className="space-y-1">
                  <div className="flex justify-between items-center">
                    <label className="text-[10px] text-white/40 uppercase tracking-widest font-semibold block">
                      Stationary Lock Radius (m)
                    </label>
                    <span className="text-[9px] text-white/30 font-mono">Default: 15</span>
                  </div>
                  <input
                    type="number"
                    step="1"
                    min="1"
                    max="100"
                    value={lockRadius}
                    onChange={(e) => setLockRadius(Number(e.target.value))}
                    className="w-full bg-white/5 border border-white/10 rounded-lg py-1.5 px-2.5 text-xs text-white font-mono focus:outline-none focus:border-orange-500"
                  />
                </div>

                <div className="flex gap-2 pt-1">
                  {configScope === "vehicle" && data.config && (
                    <button
                      type="button"
                      onClick={handleResetConfig}
                      disabled={resettingConfig || savingConfig}
                      className="flex-1 py-2 px-2.5 border border-red-500/30 bg-red-500/10 hover:bg-red-500/20 text-red-400 font-bold text-xs rounded-lg transition-all duration-200 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      {resettingConfig ? "Resetting..." : configReset ? "Reset!" : "Reset"}
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={handleSaveConfig}
                    disabled={savingConfig || resettingConfig}
                    className={`py-2 px-3 font-bold text-xs rounded-lg transition-all duration-200 border flex-1 text-center justify-center items-center flex gap-1.5
                      ${configSaved
                        ? "bg-green-500/20 border-green-500/30 text-green-400"
                        : "bg-orange-500 border-orange-600 hover:bg-orange-600 text-white cursor-pointer active:scale-[0.98] disabled:opacity-40 disabled:cursor-not-allowed"}`}
                  >
                    {savingConfig ? (
                      <>
                        <span className="w-3 h-3 border-2 border-white/20 border-t-white rounded-full animate-spin inline-block" />
                        Saving...
                      </>
                    ) : configSaved ? (
                      "Saved!"
                    ) : (
                      "Save Parameters"
                    )}
                  </button>
                </div>
              </div>
            </div>

            {/* Navigate button */}
            <div className="px-5 py-4 border-t border-white/10 shrink-0">
              <a
                href={buildNavUrl(data.lat, data.lng)}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center justify-center gap-2 w-full py-3 px-4
                  bg-gradient-to-r from-orange-500 to-red-600
                  hover:from-orange-400 hover:to-red-500
                  text-white font-semibold text-sm rounded-xl
                  transition-all duration-200 shadow-lg shadow-orange-500/30
                  hover:shadow-orange-500/50 active:scale-95 text-center"
              >
                <span>🧭</span>
                View on OpenStreetMap
              </a>
            </div>
          </div>
        )
      ) : (
        // LOGS TAB CONTENT
        <div className="flex-1 flex flex-col min-h-0 bg-gray-950/20">
          {/* Logs Header */}
          <div className="px-5 py-4 border-b border-white/10 bg-black/15 flex items-center justify-between shrink-0">
            <div>
              <h2 className="text-xs font-bold text-white uppercase tracking-wider">System Alerts Log</h2>
              <p className="text-[10px] text-white/40 mt-0.5 font-medium">Persistent historical logs</p>
            </div>
            {adminMode && (
              <button
                onClick={async () => {
                  try {
                    await fetch("/api/v1/tracking/logs", { method: "DELETE" });
                  } catch (e) {
                    console.error("Failed to clear logs:", e);
                  }
                }}
                className="px-2.5 py-1.5 text-[10px] font-extrabold text-red-400 bg-red-500/10 border border-red-500/20 hover:bg-red-500/20 rounded-lg cursor-pointer transition-all duration-200"
              >
                Clear Logs
              </button>
            )}
          </div>

          {/* Logs List */}
          <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2.5 scrollbar-thin">
            {Object.keys(logs).length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full text-center p-6 text-white/30">
                <span className="text-3xl mb-2">🔔</span>
                <p className="text-xs font-medium">No alerts logged yet</p>
              </div>
            ) : (
              Object.entries(logs)
                .sort((a, b) => b[0] - a[0]) // Sort by timestamp key descending
                .map(([key, log]) => {
                  const logDate = new Date(log?.timestamp || Number(key));
                  const timeStr = logDate.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

                  const severityColors = {
                    critical: "bg-red-500/10 border-red-500/20 text-red-400",
                    warning: "bg-orange-500/10 border-orange-500/20 text-orange-400",
                    normal: "bg-green-500/10 border-green-500/20 text-green-400"
                  };
                  const colors = severityColors[log?.severity] || severityColors.normal;

                  const typeLabels = {
                    GEOFENCE: "GEOFENCE ENTER",
                    GEOFENCE_EXIT: "GEOFENCE EXIT",
                    SIGNAL: "SIGNAL",
                    JAMMING: "JAMMING",
                    OFF_ROUTE: "OFF ROUTE",
                    ON_ROUTE: "ON ROUTE"
                  };
                  const typeLabel = typeLabels[log?.type] || log?.type || "INFO";

                  return (
                    <div key={key} className={`p-3 rounded-xl border flex flex-col gap-2 transition-all ${colors}`}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[9px] font-black uppercase px-2 py-0.5 rounded border border-current">
                          {typeLabel}
                        </span>
                        <span className="text-[10px] font-semibold opacity-60 font-mono">
                          {timeStr}
                        </span>
                      </div>
                      <p className="text-xs font-medium leading-relaxed opacity-95">
                        {log.message}
                      </p>
                      {log.vehicleId && (
                        <div className="flex items-center justify-between mt-0.5 border-t border-current/10 pt-2">
                          <span className="text-[10px] font-bold opacity-60">
                            ID: {log.vehicleId}
                          </span>
                          <button
                            onClick={() => onSelectVehicle(log.vehicleId)}
                            className="px-2 py-1 rounded text-[9px] font-black uppercase bg-current text-gray-950 border border-current hover:bg-transparent hover:text-current transition-all cursor-pointer"
                          >
                            🎯 Focus
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })
            )}
          </div>
        </div>
      )}
    </aside>
  );
}
