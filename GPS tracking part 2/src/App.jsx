// src/App.jsx
// Root component — wires together Firebase data, map, sidebar, and detail panel.

import { useState, useEffect, useMemo, useRef } from "react";
import { useVehicles } from "./hooks/useVehicles";
import { Sidebar } from "./components/Sidebar/Sidebar";
import { MapView } from "./components/MapView/MapView";
import { DetailPanel } from "./components/DetailPanel/DetailPanel";
import { LoadingScreen } from "./components/LoadingScreen/LoadingScreen";
import { YATRA_ROUTE, LANDMARKS, vehicleLabels } from "./lib/constants";
import { haversine, fetchLiveDistanceRoute } from "./utils/routeSnap";

export default function App() {
  // Route drawn exactly on YATRA_ROUTE coordinates — no OSRM road snapping
  const roadRoute = YATRA_ROUTE;
  const [useSnapping, setUseSnapping] = useState(true);
  const [isAndroidAuto, setIsAndroidAuto] = useState(false);
  const [mapRotationMode, setMapRotationMode] = useState("course-up"); // "course-up" or "north-up"
  const [currentTime, setCurrentTime] = useState(new Date());
  const [distanceToolEnabled, setDistanceToolEnabled] = useState(false);
  const [distanceSource, setDistanceSource] = useState("");
  const [distanceTarget, setDistanceTarget] = useState("");
  const [adminMode, setAdminMode] = useState(false);

  const { vehicles: firebaseVehicles, alerts, loading, error } = useVehicles(roadRoute, useSnapping);
  const firebaseVehiclesRef = useRef(firebaseVehicles);
  useEffect(() => {
    firebaseVehiclesRef.current = firebaseVehicles;
  }, [firebaseVehicles]);

  const [selectedId, setSelectedId] = useState(null);
  const [useCompass, setUseCompass] = useState(true);

  const [playbackMode, setPlaybackMode] = useState(false);
  const [playbackIndex, setPlaybackIndex] = useState(0);
  const [playbackSpeed, setPlaybackSpeed] = useState(2);
  const [playbackIsPlaying, setPlaybackIsPlaying] = useState(false);
  const [playbackVehicleId, setPlaybackVehicleId] = useState("");
  const [playbackDate, setPlaybackDate] = useState(() => {
    // Current IST date
    const d = new Date();
    const ist = new Date(d.getTime() + 5.5 * 3600 * 1000);
    return ist.toISOString().split("T")[0];
  });
  const [playbackStartTime, setPlaybackStartTime] = useState("");
  const [playbackEndTime, setPlaybackEndTime] = useState("");
  const [loadingPlayback, setLoadingPlayback] = useState(false);
  const [playbackRoutePoints, setPlaybackRoutePoints] = useState([]);
  const [totalRawPoints, setTotalRawPoints] = useState(0);
  const [selectedRecordVehicleId, setSelectedRecordVehicleId] = useState("");
  const [selectedRecordingKey, setSelectedRecordingKey] = useState(""); // Set to "mongo" when mongo playback loads

  // Default playback vehicle ID once on entering playback mode
  useEffect(() => {
    if (playbackMode && !playbackVehicleId) {
      if (selectedId) {
        setPlaybackVehicleId(selectedId);
      } else {
        setPlaybackVehicleId("TRUCK_1");
      }
    }
  }, [playbackMode]);

  const loadMongoPlayback = async (vehicleId, dateStr, startTime, endTime) => {
    if (!vehicleId || !dateStr) {
      alert("Please select a vehicle and a date first.");
      return;
    }
    setLoadingPlayback(true);
    try {
      let url = `/api/get_map_recordings_data?device_id=${encodeURIComponent(vehicleId)}&date=${encodeURIComponent(dateStr)}`;
      if (startTime) url += `&start_time=${encodeURIComponent(startTime)}`;
      if (endTime) url += `&end_time=${encodeURIComponent(endTime)}`;
      
      const res = await fetch(url);
      const data = await res.json();
      if (data.points && data.points.length > 0) {
        setPlaybackRoutePoints(data.points);
        setTotalRawPoints(data.total_raw || data.points.length);
        setPlaybackIndex(0);
        setSelectedRecordVehicleId(vehicleId);
        setPlaybackIsPlaying(true);
        setSelectedRecordingKey("mongo");
      } else {
        alert(`No GPS coordinates found for "${vehicleId}" in the selected range.`);
        setPlaybackRoutePoints([]);
        setTotalRawPoints(0);
        setPlaybackIndex(0);
        setPlaybackIsPlaying(false);
        setSelectedRecordingKey("");
      }
    } catch (e) {
      console.error("Failed to load playback data:", e);
      alert("Connection error: Failed to fetch playback data from MongoDB.");
    } finally {
      setLoadingPlayback(false);
    }
  };

  // Auto-load playback data when vehicle, date, or times change
  useEffect(() => {
    if (playbackMode && playbackVehicleId && playbackDate) {
      loadMongoPlayback(playbackVehicleId, playbackDate, playbackStartTime, playbackEndTime);
    }
  }, [playbackMode, playbackVehicleId, playbackDate, playbackStartTime, playbackEndTime]);

  // Poll persistent logs from MongoDB
  const [logs, setLogs] = useState({});
  useEffect(() => {
    const fetchLogs = async () => {
      try {
        const res = await fetch("/api/v1/tracking/logs");
        const data = await res.json();
        setLogs(data || {});
      } catch (e) {
        console.error("Failed to fetch logs:", e);
      }
    };
    fetchLogs();
    const interval = setInterval(fetchLogs, 10000);
    return () => clearInterval(interval);
  }, []);
  // Fetch full device list from MongoDB for playback selection
  const [deviceList, setDeviceList] = useState([]);
  useEffect(() => {
    const fetchDevices = async () => {
      try {
        const res = await fetch("/api/get_devices_list");
        const data = await res.json();
        if (Array.isArray(data)) {
          setDeviceList(data);
        }
      } catch (e) {
        console.error("Failed to fetch device list:", e);
      }
    };
    fetchDevices();
  }, []);


  const [hidePlaybackTrail, setHidePlaybackTrail] = useState(false);

  // Reset hidePlaybackTrail when playing starts, key changes, or playback index changes back
  useEffect(() => {
    if (playbackIsPlaying || playbackIndex < playbackRoutePoints.length - 1) {
      setHidePlaybackTrail(false);
    }
  }, [playbackIsPlaying, playbackIndex, playbackRoutePoints.length]);

  // Set 5 seconds timeout to hide trail when playback finishes
  useEffect(() => {
    if (!playbackMode || playbackRoutePoints.length === 0) return;
    
    if (!playbackIsPlaying && playbackIndex >= playbackRoutePoints.length - 1) {
      const timer = setTimeout(() => {
        setHidePlaybackTrail(true);
      }, 5000); // 5 seconds
      return () => clearTimeout(timer);
    }
  }, [playbackMode, playbackIsPlaying, playbackIndex, playbackRoutePoints.length]);



  // Reset index when path changes
  useEffect(() => {
    setPlaybackIndex(0);
    setPlaybackIsPlaying(false);
  }, [selectedRecordingKey]);

  // Playback animation timer
  useEffect(() => {
    if (!playbackMode || !playbackIsPlaying || playbackRoutePoints.length === 0) return;

    const timer = setInterval(() => {
      setPlaybackIndex((prevIndex) => {
        const nextIndex = prevIndex + 1;
        if (nextIndex >= playbackRoutePoints.length) {
          setPlaybackIsPlaying(false); // Pause at end of route
          return prevIndex;
        }
        return nextIndex;
      });
    }, 1000 / playbackSpeed);

    return () => clearInterval(timer);
  }, [playbackMode, playbackIsPlaying, playbackRoutePoints, playbackSpeed]);

  // Intercept vehicles and mock positions if playbackMode is active
  const vehicles = useMemo(() => {
    if (!playbackMode || playbackRoutePoints.length === 0 || !selectedRecordVehicleId) {
      return firebaseVehicles;
    }

    const mocked = { ...firebaseVehicles };
    const currentPoint = playbackRoutePoints[playbackIndex];
    if (currentPoint) {
      const vid = selectedRecordVehicleId;
      
      // Calculate bearing to next point if not present
      let bearing = currentPoint.bearing || 0;
      if (bearing === 0 && playbackIndex < playbackRoutePoints.length - 1) {
        const nextPoint = playbackRoutePoints[playbackIndex + 1];
        const lat1 = currentPoint.lat, lng1 = currentPoint.lng;
        const lat2 = nextPoint.lat, lng2 = nextPoint.lng;
        const dLng = (lng2 - lng1) * Math.PI / 180;
        const lat1Rad = lat1 * Math.PI / 180;
        const lat2Rad = lat2 * Math.PI / 180;
        const y = Math.sin(dLng) * Math.cos(lat2Rad);
        const x = Math.cos(lat1Rad) * Math.sin(lat2Rad) - Math.sin(lat1Rad) * Math.cos(lat2Rad) * Math.cos(dLng);
        bearing = ((Math.atan2(y, x) * 180 / Math.PI) + 360) % 360;
      }

      mocked[vid] = {
        display_name: vehicleLabels[vid] || vid,
        icon: "🚛",
        ...firebaseVehicles[vid],
        key: vid,
        id: vid,
        lat: currentPoint.lat,
        lng: currentPoint.lng,
        rawLat: currentPoint.lat,
        rawLng: currentPoint.lng,
        bearing,
        speed: currentPoint.speed ?? 0,
        speed_kmh: currentPoint.speed ?? 0,
        is_moving: playbackIsPlaying,
        online: true,
        status: "online",
        satellites: currentPoint.satellites ?? 12,
        hdop: currentPoint.hdop ?? 1.0,
        timestamp: (currentPoint.timestamp && typeof currentPoint.timestamp === "string")
          ? currentPoint.timestamp.replace("T", " ").slice(0, 19) 
          : new Date().toISOString().replace("T", " ").slice(0, 19),
        // Use the recorded trail up to the current playback index
        trail: hidePlaybackTrail ? [] : playbackRoutePoints.slice(0, playbackIndex + 1).map(p => [p.lat, p.lng]),
        rawTrail: hidePlaybackTrail ? [] : playbackRoutePoints.slice(0, playbackIndex + 1).map(p => [p.lat, p.lng])
      };
    }
    return mocked;
  }, [firebaseVehicles, playbackMode, playbackRoutePoints, playbackIndex, selectedRecordVehicleId, playbackIsPlaying, hidePlaybackTrail]);

  const liveDistanceMeters = useMemo(() => {
    if (!distanceToolEnabled || !distanceSource || !distanceTarget) return null;
    const v1 = vehicles?.[distanceSource];
    const v2 = vehicles?.[distanceTarget];
    if (!v1 || !v2 || typeof v1.lat !== "number" || typeof v2.lat !== "number" || typeof v1.lng !== "number" || typeof v2.lng !== "number") {
      return null;
    }
    return haversine(v1.lat, v1.lng, v2.lat, v2.lng);
  }, [distanceToolEnabled, distanceSource, distanceTarget, vehicles]);

  // Procession Analytics HUD calculations
  const processionAnalytics = useMemo(() => {
    const onlineVehicles = Object.values(vehicles).filter(v => v.online && typeof v.lat === "number");

    // 1. Average Fleet Speed
    let avgSpeed = 0;
    if (onlineVehicles.length > 0) {
      const moving = onlineVehicles.filter(v => v.is_moving);
      const speedSource = moving.length > 0 ? moving : onlineVehicles;
      avgSpeed = speedSource.reduce((sum, v) => sum + (v.speed || 0), 0) / speedSource.length;
    }
    const avgSpeedVal = `${Math.round(avgSpeed)} km/h`;

    // 2. Active Procession Spread
    let spreadVal = "0 m";
    if (onlineVehicles.length >= 2) {
      const routeMetersList = onlineVehicles.map(v => v.routeMeters || 0).sort((a, b) => a - b);
      const spreadMeters = routeMetersList[routeMetersList.length - 1] - routeMetersList[0];
      if (spreadMeters >= 1000) {
        spreadVal = `${(spreadMeters / 1000).toFixed(2)} km`;
      } else {
        spreadVal = `${Math.round(spreadMeters)} m`;
      }
    } else if (onlineVehicles.length === 1) {
      spreadVal = "Unified";
    } else {
      spreadVal = "Offline";
    }

    // 3. Next Landmark & ETA
    let nextLandmarkVal = "Waiting for GPS...";
    if (onlineVehicles.length > 0) {
      const leadVehicle = onlineVehicles.reduce((lead, v) => {
        return (v.routeMeters || 0) > (lead.routeMeters || 0) ? v : lead;
      }, onlineVehicles[0]);

      const leadMeters = leadVehicle.routeMeters || 0;
      const routePoints = playbackMode ? playbackRoutePoints : ((roadRoute && roadRoute.length > 0) ? roadRoute : YATRA_ROUTE);

      // Snapped distance (meters) along the route for each landmark
      const getLandmarkRouteMeters = (coords) => {
        let bestIdx = 0;
        let minDist = Infinity;
        for (let i = 0; i < routePoints.length; i++) {
          const d = haversine(routePoints[i][0], routePoints[i][1], coords[0], coords[1]);
          if (d < minDist) {
            minDist = d;
            bestIdx = i;
          }
        }
        
        let dist = 0;
        for (let i = 1; i <= bestIdx; i++) {
          dist += haversine(routePoints[i - 1][0], routePoints[i - 1][1], routePoints[i][0], routePoints[i][1]);
        }
        return dist;
      };

      const landmarkDistances = LANDMARKS.map(l => ({
        name: l.name,
        meters: getLandmarkRouteMeters(l.coords)
      })).sort((a, b) => a.meters - b.meters);

      // Find the next landmark ahead
      let nextL = landmarkDistances.find(l => l.meters > leadMeters);
      if (!nextL && landmarkDistances.length > 0) {
        nextL = landmarkDistances[0];
      }

      if (nextL) {
        let remainingMeters = nextL.meters - leadMeters;
        if (remainingMeters < 0 && landmarkDistances.length > 0) {
          const totalLength = landmarkDistances[landmarkDistances.length - 1].meters;
          remainingMeters = (totalLength - leadMeters) + nextL.meters;
        }

        const speedKmh = leadVehicle.speed || avgSpeed || 5; // Default walking speed 5km/h
        const speedMps = speedKmh / 3.6;
        const etaSeconds = remainingMeters / speedMps;
        const etaMins = Math.round(etaSeconds / 60);

        const etaStr = etaMins > 120 
          ? "> 2h" 
          : etaMins > 0 
            ? `${etaMins}m` 
            : "Arriving";

        // Extract short name for landmarks like "Jagannath Temple, Jamalpur (Start)" -> "Jagannath Temple"
        const cleanName = nextL.name.split(",")[0].trim();
        nextLandmarkVal = `${cleanName} (${etaStr})`;
      } else {
        nextLandmarkVal = "Procession Complete";
      }
    }

    return {
      spread: spreadVal,
      avgSpeed: avgSpeedVal,
      nextLandmark: nextLandmarkVal
    };
  }, [vehicles, roadRoute, playbackRoutePoints, playbackMode]);

  // Initialize distanceSource and distanceTarget with the first two vehicle IDs
  useEffect(() => {
    if (!vehicles) return;
    const ids = Object.keys(vehicles);
    if (ids.length >= 2) {
      if (!distanceSource) setDistanceSource(ids[0]);
      if (!distanceTarget) setDistanceTarget(ids[1]);
    }
  }, [vehicles, distanceSource, distanceTarget]);

  // roadRoute = YATRA_ROUTE (exact GPS coordinates, no road snapping)

  // Update clock every second for Android Auto Bottom Bar
  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  // Auto-select first active vehicle in HUD mode
  useEffect(() => {
    if (isAndroidAuto && !selectedId) {
      const activeIds = Object.keys(vehicles);
      const onlineId = activeIds.find(id => vehicles[id]?.online && typeof vehicles[id]?.lat === "number");
      const anyId = activeIds.find(id => typeof vehicles[id]?.lat === "number");
      if (onlineId) {
        setSelectedId(onlineId);
      } else if (anyId) {
        setSelectedId(anyId);
      }
    }
  }, [isAndroidAuto, selectedId, vehicles]);

  const handleSelect = (id) => {
    setSelectedId((prev) => (prev === id ? null : id));
  };

  const selectedVehicle = selectedId ? vehicles[selectedId] : null;

  // Compute ordered trucks and exact distances to front/back neighbors for map rendering
  const orderedTrucks = useMemo(() => {
    const list = Object.entries(vehicles)
      .filter(([, v]) => v && typeof v.lat === "number" && typeof v.lng === "number")
      .map(([id, v]) => ({ id, ...v }));

    const offRouteCount = list.filter(v => v.offRoute).length;
    const isProcessionOffRoute = list.length > 0 && (offRouteCount / list.length) > 0.5;

    if (isProcessionOffRoute) {
      // Calculate average bearing (circular mean)
      let sinSum = 0;
      let cosSum = 0;
      let count = 0;
      list.forEach(v => {
        if (typeof v.bearing === "number") {
          const rad = v.bearing * Math.PI / 180;
          sinSum += Math.sin(rad);
          cosSum += Math.cos(rad);
          count++;
        }
      });
      const avgBearingRad = count > 0 ? Math.atan2(sinSum, cosSum) : 0;
      const dx = Math.sin(avgBearingRad);
      const dy = Math.cos(avgBearingRad);

      // Sort by projection onto the average bearing vector descending
      return list.sort((a, b) => {
        const projA = a.lng * dx + a.lat * dy;
        const projB = b.lng * dx + b.lat * dy;
        return projB - projA;
      });
    } else {
      // Standard routeMeters sorting
      return list.sort((a, b) => (b.routeMeters || 0) - (a.routeMeters || 0));
    }
  }, [vehicles]);

  const [frontBackRoutes, setFrontBackRoutes] = useState([]);
  const lastFrontFetch = useRef({ t1: null, t2: null, time: 0, cachedRes: null });
  const lastBackFetch = useRef({ t1: null, t2: null, time: 0, cachedRes: null });

  useEffect(() => {
    if (!selectedId) {
      setFrontBackRoutes([]);
      return;
    }
    const selectedIdx = orderedTrucks.findIndex((t) => t.id === selectedId);
    if (selectedIdx === -1) {
      setFrontBackRoutes([]);
      return;
    }

    const selected = orderedTrucks[selectedIdx];
    const tFront = selectedIdx > 0 ? orderedTrucks[selectedIdx - 1] : null;
    const tBack = selectedIdx < orderedTrucks.length - 1 ? orderedTrucks[selectedIdx + 1] : null;

    const tFrontName = tFront ? (tFront.display_name || vehicleLabels[tFront.id] || tFront.id) : "";
    const tBackName = tBack ? (tBack.display_name || vehicleLabels[tBack.id] || tBack.id) : "";

    if (playbackMode) {
      const routePoints = selectedRecordingKey === "yatra" ? (roadRoute && roadRoute.length > 0 ? roadRoute : YATRA_ROUTE) : playbackRoutePoints;
      const newRoutes = [];
      const routeLength = routePoints.length;
      
      if (routeLength > 0) {
        const sIdx = selected.id === selectedRecordVehicleId ? playbackIndex % routeLength : 0;
        
        if (tFront) {
           const fIdx = tFront.id === selectedRecordVehicleId ? playbackIndex % routeLength : 0;
           const dist = (tFront.routeMeters || 0) - (selected.routeMeters || 0);
           
           let slice = [];
           if (fIdx >= sIdx) slice = routePoints.slice(sIdx, fIdx + 1);
           else slice = [...routePoints.slice(sIdx), ...routePoints.slice(0, fIdx + 1)];
           
           const geometry = slice.map(p => Array.isArray(p) ? p : (p && typeof p.lat === "number" ? [p.lat, p.lng] : null)).filter(Boolean);
           
           newRoutes.push({
             geometry: geometry.length > 1 ? geometry : [[selected.lat, selected.lng], [tFront.lat, tFront.lng]],
             distanceMeters: Math.max(0, dist),
             label: `Front: ${tFrontName}`
           });
        }
        
        if (tBack) {
           const bIdx = tBack.id === selectedRecordVehicleId ? playbackIndex % routeLength : 0;
           const dist = (selected.routeMeters || 0) - (tBack.routeMeters || 0);
           
           let slice = [];
           if (sIdx >= bIdx) slice = routePoints.slice(bIdx, sIdx + 1);
           else slice = [...routePoints.slice(bIdx), ...routePoints.slice(0, sIdx + 1)];
           
           const geometry = slice.map(p => Array.isArray(p) ? p : (p && typeof p.lat === "number" ? [p.lat, p.lng] : null)).filter(Boolean);
           
           newRoutes.push({
             geometry: geometry.length > 1 ? geometry : [[selected.lat, selected.lng], [tBack.lat, tBack.lng]],
             distanceMeters: Math.max(0, dist),
             label: `Back: ${tBackName}`
           });
        }
      }
      setFrontBackRoutes(newRoutes);
      return;
    }

    // Real-time tracking mode
    const now = Date.now();
    const promises = [];
    const fallbackRoutes = [];
    
    if (tFront) {
       const dist = (tFront.routeMeters || 0) - (selected.routeMeters || 0);
       const fLast = lastFrontFetch.current;
       const movedT1 = !fLast.t1 || haversine(fLast.t1.lat, fLast.t1.lng, selected.lat, selected.lng) > 5;
       const movedT2 = !fLast.t2 || haversine(fLast.t2.lat, fLast.t2.lng, tFront.lat, tFront.lng) > 5;
       
       if ((movedT1 || movedT2) && (now - fLast.time) > 3000) {
         promises.push(
           fetchLiveDistanceRoute(selected.lat, selected.lng, tFront.lat, tFront.lng).then(res => {
             if (res && res.geometry) {
               lastFrontFetch.current = { t1: {lat: selected.lat, lng: selected.lng}, t2: {lat: tFront.lat, lng: tFront.lng}, time: Date.now(), cachedRes: res };
               return { ...res, label: `Front: ${tFrontName}` };
             }
             return { geometry: [[selected.lat, selected.lng], [tFront.lat, tFront.lng]], distanceMeters: Math.max(0, dist), label: `Front: ${tFrontName}` };
           })
         );
       } else if (fLast.cachedRes) {
         fallbackRoutes.push({ ...fLast.cachedRes, label: `Front: ${tFrontName}` });
       } else {
         fallbackRoutes.push({ geometry: [[selected.lat, selected.lng], [tFront.lat, tFront.lng]], distanceMeters: Math.max(0, dist), label: `Front: ${tFrontName}` });
       }
    }
    
    if (tBack) {
       const dist = (selected.routeMeters || 0) - (tBack.routeMeters || 0);
       const bLast = lastBackFetch.current;
       const movedT1 = !bLast.t1 || haversine(bLast.t1.lat, bLast.t1.lng, selected.lat, selected.lng) > 5;
       const movedT2 = !bLast.t2 || haversine(bLast.t2.lat, bLast.t2.lng, tBack.lat, tBack.lng) > 5;
       
       if ((movedT1 || movedT2) && (now - bLast.time) > 3000) {
         promises.push(
           fetchLiveDistanceRoute(selected.lat, selected.lng, tBack.lat, tBack.lng).then(res => {
             if (res && res.geometry) {
               lastBackFetch.current = { t1: {lat: selected.lat, lng: selected.lng}, t2: {lat: tBack.lat, lng: tBack.lng}, time: Date.now(), cachedRes: res };
               return { ...res, label: `Back: ${tBackName}` };
             }
             return { geometry: [[selected.lat, selected.lng], [tBack.lat, tBack.lng]], distanceMeters: Math.max(0, dist), label: `Back: ${tBackName}` };
           })
         );
       } else if (bLast.cachedRes) {
         fallbackRoutes.push({ ...bLast.cachedRes, label: `Back: ${tBackName}` });
       } else {
         fallbackRoutes.push({ geometry: [[selected.lat, selected.lng], [tBack.lat, tBack.lng]], distanceMeters: Math.max(0, dist), label: `Back: ${tBackName}` });
       }
    }
    
    if (promises.length > 0) {
      Promise.all(promises).then(results => {
        setFrontBackRoutes([...fallbackRoutes, ...results]);
      });
    } else {
      setFrontBackRoutes(fallbackRoutes);
    }
    
  }, [selectedId, orderedTrucks, playbackMode, playbackIndex, selectedRecordVehicleId, playbackRoutePoints, roadRoute, selectedRecordingKey]);

  if (loading) return <LoadingScreen />;

  // Helper to compute Turn-by-Turn Guidance instructions for Android Auto HUD
  const getGuidance = (vehicle) => {
    if (!vehicle) return { instruction: "Searching...", sub: "Wait for GPS lock", icon: "🗺️" };
    if (!vehicle.online) return { instruction: "Vehicle Offline", sub: "Waiting for signal", icon: "📡" };
    
    return {
      instruction: "Follow Campus Route",
      sub: vehicle.offRoute ? "⚠️ OUT OF RANGE" : "Within Route Range",
      icon: vehicle.offRoute ? "⚠️" : "⬆️"
    };
  };

  const getSpacingAlert = (vehicle) => {
    if (!vehicle) return null;
    const vehicleAlert = (alerts || []).find(a => a.trucks && a.trucks.includes(vehicle.key));
    if (vehicleAlert) {
      return {
        message: vehicleAlert.message,
        type: vehicleAlert.type,
        severity: vehicleAlert.severity // "critical" or "warning"
      };
    }
    return {
      message: "Procession gap stable",
      type: "NORMAL",
      severity: "normal"
    };
  };

  const formatTime = (date) => {
    return date.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  };

  // ── Render Android Auto Immersive HUD View ──────────────────────────────────
  if (isAndroidAuto) {
    const guidance = getGuidance(selectedVehicle);
    const spacingAlert = getSpacingAlert(selectedVehicle);
    const activeVehiclesCount = Object.values(vehicles).filter(v => v.online).length;

    return (
      <div className="fixed inset-0 bg-gray-950 overflow-hidden flex flex-col font-sans select-none z-[5000]">
        
        {/* Full Screen Map View */}
        <div className="flex-1 w-full h-full absolute inset-0 z-0">
          <MapView
            vehicles={vehicles}
            selectedId={selectedId}
            onVehicleSelect={handleSelect}
            yatraRoute={roadRoute}
            rawYatraRoute={YATRA_ROUTE}
            useSnapping={useSnapping}
            onToggleSnapping={setUseSnapping}
            useCompass={useCompass}
            onToggleCompass={setUseCompass}
            isAndroidAuto={true}
            mapRotationMode={mapRotationMode}
            onToggleRotationMode={() => setMapRotationMode(m => m === "course-up" ? "north-up" : "course-up")}
            onToggleAndroidAuto={setIsAndroidAuto}
            frontBackRoutes={frontBackRoutes}
          />
        </div>

        {/* HUD OVERLAYS - Top Left (Guidance Card) */}
        <div className="absolute top-4 left-4 z-[1000] w-80 bg-gray-900/90 backdrop-blur-xl border border-white/10 rounded-2xl p-4 shadow-2xl flex items-center gap-4 transition-all duration-300">
          <div className="w-12 h-12 rounded-full bg-green-500/20 text-green-400 border border-green-500/30 flex items-center justify-center text-2xl font-bold animate-pulse">
            {guidance.icon}
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-sm font-bold text-white uppercase tracking-wide leading-tight truncate">
              {guidance.instruction}
            </h2>
            <p className="text-xs text-white/60 font-semibold truncate mt-0.5">
              {guidance.sub}
            </p>
          </div>
        </div>

        {/* HUD OVERLAYS - Top Right (Procession Spacing / Alert Card) */}
        <div className="absolute top-4 right-4 z-[1000] w-80 bg-gray-900/90 backdrop-blur-xl border border-white/10 rounded-2xl p-4 shadow-2xl space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-bold text-white/40 uppercase tracking-widest">Procession Spacing</span>
            <div className={`w-2.5 h-2.5 rounded-full ${
              spacingAlert?.severity === "critical" ? "bg-red-500 animate-ping" : 
              spacingAlert?.severity === "warning" ? "bg-orange-500 animate-pulse" : "bg-green-500"
            }`} />
          </div>
          <div>
            <p className={`text-xs font-bold leading-snug ${
              spacingAlert?.severity === "critical" ? "text-red-400" :
              spacingAlert?.severity === "warning" ? "text-orange-400" : "text-white"
            }`}>
              {spacingAlert?.message || "All vehicles in range"}
            </p>
            <p className="text-[10px] text-white/50 mt-1 font-medium">
              Procession Status: {activeVehiclesCount} of {Object.keys(vehicles).length} trucks active
            </p>
          </div>
        </div>

        {/* HUD OVERLAYS - Bottom Left (Speedometer overlay) */}
        {selectedVehicle && (
          <div className="absolute bottom-20 left-4 z-[1000] bg-gray-900/90 backdrop-blur-xl border border-white/10 rounded-2xl p-4 shadow-2xl flex items-center gap-3">
            <div className="flex flex-col items-center">
              <span className="text-3xl font-black text-green-400 drop-shadow-[0_0_10px_rgba(74,222,128,0.4)]">
                {selectedVehicle.speed || 0}
              </span>
              <span className="text-[8px] font-bold text-white/50 uppercase tracking-wider -mt-1">km/h</span>
            </div>
            <div className="h-8 w-px bg-white/15" />
            <div>
              <p className="text-[9px] font-black text-white/40 uppercase tracking-wider">Speed State</p>
              <p className="text-xs font-bold text-white mt-0.5">
                {selectedVehicle.is_moving ? "🚗 Traveling" : "🅿️ Parked"}
              </p>
            </div>
          </div>
        )}

        {/* HUD OVERLAYS - Bottom Right (GPS / Status Overlay) */}
        {selectedVehicle && (
          <div className="absolute bottom-20 right-4 z-[1000] bg-gray-900/90 backdrop-blur-xl border border-white/10 rounded-2xl p-3 shadow-2xl flex flex-col gap-1 text-right">
            <p className="text-[9px] font-black text-white/40 uppercase tracking-wider">Device Status</p>
            <p className="text-xs font-bold text-white flex items-center justify-end gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-green-400" />
              {selectedVehicle.key}
            </p>
            <p className="text-[9px] text-white/60 font-semibold font-mono mt-0.5">
              🛰️ {selectedVehicle.satellites || 0} Sats | HDOP {selectedVehicle.hdop ? Number(selectedVehicle.hdop).toFixed(1) : "—"}
            </p>
          </div>
        )}

        {/* Android Auto Immersive Bottom Taskbar */}
        <div className="w-full h-16 bg-gray-950 border-t border-white/5 px-6 flex items-center justify-between z-[2000] absolute bottom-0 left-0 right-0">
          {/* Exit HUD Button */}
          <button
            onClick={() => setIsAndroidAuto(false)}
            className="flex items-center gap-2 px-4 py-2 rounded-xl bg-red-600/20 border border-red-500/30 hover:bg-red-600/30 active:scale-[0.98] transition-all text-red-400 text-xs font-black cursor-pointer"
          >
            ❌ Exit HUD View
          </button>

          {/* Center Details & Controllers */}
          <div className="flex items-center gap-3">
            <button
              onClick={() => setMapRotationMode(m => m === "course-up" ? "north-up" : "course-up")}
              className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-white/5 border border-white/10 text-white hover:bg-white/10 transition-all text-xs font-black cursor-pointer"
            >
              🧭 {mapRotationMode === "course-up" ? "Course Up" : "North Up"}
            </button>
            <div className="h-4 w-px bg-white/15" />
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-bold text-white/50 uppercase tracking-widest">Active Vehicle:</span>
              <select
                value={selectedId || ""}
                onChange={(e) => setSelectedId(e.target.value)}
                className="bg-gray-900 border border-white/15 rounded-lg px-2.5 py-1 text-xs font-bold text-white focus:outline-none focus:border-orange-500 cursor-pointer"
              >
                {Object.keys(vehicles).map(id => (
                  <option key={id} value={id}>
                    {id} ({vehicles[id]?.online ? "Online" : "Offline"})
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Clock & Connection Signals */}
          <div className="flex items-center gap-5">
            <div className="flex items-center gap-2 text-white/60">
              <span className="text-xs font-mono font-bold tracking-wider">{formatTime(currentTime)}</span>
            </div>
            <div className="h-4 w-px bg-white/15" />
            <div className="flex items-center gap-1 text-xs text-white/40">
              <span className="text-[10px] font-black text-green-400">📶 Signal OK</span>
            </div>
          </div>
        </div>

      </div>
    );
  }

  return (
    <div className="fixed inset-0 flex bg-gray-950 overflow-hidden">
      {/* Sidebar — vehicle list */}
      <div className="w-64 shrink-0 flex flex-col">
        <Sidebar
          vehicles={vehicles}
          selectedId={selectedId}
          onSelect={handleSelect}
          adminMode={adminMode}
          onToggleAdminMode={setAdminMode}
          frontBackRoutes={frontBackRoutes}
          orderedTrucks={orderedTrucks}
        />
      </div>

      {/* Main map area */}
      <div className="flex-1 flex flex-col relative">
        <MapView
          vehicles={vehicles}
          selectedId={selectedId}
          onVehicleSelect={handleSelect}
          yatraRoute={roadRoute}
          rawYatraRoute={YATRA_ROUTE}
          useSnapping={useSnapping}
          onToggleSnapping={setUseSnapping}
          useCompass={useCompass}
          onToggleCompass={setUseCompass}
          distanceToolEnabled={distanceToolEnabled}
          onDistanceToolEnabledChange={setDistanceToolEnabled}
          distanceSource={distanceSource}
          onDistanceSourceChange={setDistanceSource}
          distanceTarget={distanceTarget}
          onDistanceTargetChange={setDistanceTarget}
          liveDistanceMeters={liveDistanceMeters}
          isAndroidAuto={false}
          onToggleAndroidAuto={setIsAndroidAuto}
          frontBackRoutes={frontBackRoutes}
          playbackMode={playbackMode}
          onTogglePlaybackMode={setPlaybackMode}
          playbackIndex={playbackIndex}
          onPlaybackIndexChange={setPlaybackIndex}
          playbackIsPlaying={playbackIsPlaying}
          onPlaybackIsPlayingChange={setPlaybackIsPlaying}
          playbackSpeed={playbackSpeed}
          onPlaybackSpeedChange={setPlaybackSpeed}
          processionAnalytics={processionAnalytics}
          playbackVehicleId={playbackVehicleId}
          onPlaybackVehicleIdChange={setPlaybackVehicleId}
          playbackDate={playbackDate}
          onPlaybackDateChange={setPlaybackDate}
          playbackStartTime={playbackStartTime}
          onPlaybackStartTimeChange={setPlaybackStartTime}
          playbackEndTime={playbackEndTime}
          onPlaybackEndTimeChange={setPlaybackEndTime}
          loadingPlayback={loadingPlayback}
          onLoadPlayback={loadMongoPlayback}
          selectedRecordingKey={selectedRecordingKey}
          onSelectedRecordingKeyChange={setSelectedRecordingKey}
          selectedRecordVehicleId={selectedRecordVehicleId}
          deviceList={deviceList}
          totalRawPoints={totalRawPoints}
          playbackRoutePoints={playbackRoutePoints}
        />
      </div>

      {/* Detail panel */}
      <div className="w-72 shrink-0 flex flex-col">
        <DetailPanel 
          vehicleId={selectedId} 
          data={selectedVehicle} 
          useCompass={useCompass}
          onToggleCompass={setUseCompass}
          adminMode={adminMode}
          logs={logs}
          onSelectVehicle={handleSelect}
        />
      </div>
    </div>
  );
}
