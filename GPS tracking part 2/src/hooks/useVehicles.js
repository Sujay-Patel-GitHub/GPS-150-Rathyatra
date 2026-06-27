// src/hooks/useVehicles.js
// Real-time Firebase subscription for all 150 vehicles.
// Subscribes to the /vehicles node once and returns raw data (no filters, no snapping).

import { useEffect, useState, useRef, useMemo } from "react";
import { ref, onValue, off, set } from "firebase/database";
import { db } from "../lib/firebase";
import { ACTIVE_VEHICLE_IDS, OFFLINE_THRESHOLD_SECONDS, ALERT, YATRA_ROUTE, DEVICE_TO_DISPLAY_MAP, LANDMARKS, TRUCK_PHONES } from "../lib/constants";
import { parseTimestamp } from "../utils/formatters";
import { snapToRoute, haversine } from "../utils/routeSnap";

const MAX_TRAIL = 300;



function getStatus(data) {
  // No Firebase data at all — device never connected
  if (!data || typeof data.lat !== "number") return "offline";

  const age = (Date.now() - parseTimestamp(data.timestamp).getTime()) / 1000;

  // 1. Age/Inactivity checks must come first to detect disconnects
  if (age > OFFLINE_THRESHOLD_SECONDS) return "offline";
  if (age > ALERT.SIGNAL_LOST_SEC) return "lost";

  // 2. Active status flags
  if (data.is_jammed) return "jammed";
  if (data.hdop > ALERT.HDOP_WARN || data.is_estimated) return "weak";
  return "online";
}

function loadLastLocation(id) {
  try {
    const val = localStorage.getItem(`last_loc_${id}`);
    if (val) {
      return JSON.parse(val);
    }
  } catch (e) {
    console.error(`Failed to load last location for ${id}:`, e);
  }
  return null;
}

function findClosestIndex(roadRoute, point) {
  if (!roadRoute || roadRoute.length === 0) return 0;
  let minIdx = 0;
  let minDist = Infinity;
  for (let i = 0; i < roadRoute.length; i++) {
    const d = haversine(roadRoute[i][0], roadRoute[i][1], point[0], point[1]);
    if (d < minDist) {
      minDist = d;
      minIdx = i;
    }
  }
  return minIdx;
}

function isPointOnRoute(roadRoute, point) {
  if (!roadRoute || roadRoute.length === 0) return false;
  const idx = findClosestIndex(roadRoute, point);
  const dist = haversine(roadRoute[idx][0], roadRoute[idx][1], point[0], point[1]);
  return dist < 15; // Within 15 meters
}

function sliceRoadRoute(roadRoute, p1, p2) {
  if (!roadRoute || roadRoute.length < 2) return [p2];
  const idx1 = findClosestIndex(roadRoute, p1);
  const idx2 = findClosestIndex(roadRoute, p2);

  if (idx1 === idx2) {
    return [p2];
  }

  if (idx1 < idx2) {
    return roadRoute.slice(idx1 + 1, idx2 + 1);
  } else {
    // If moving backwards or GPS fluctuations, just return the target point directly
    // to avoid appending reversed path segments that cause zig-zag trails
    return [p2];
  }
}

export function useVehicles(snappingRoute = YATRA_ROUTE, useSnapping = true) {
  const [rawVehicles, setRawVehicles] = useState({});
  const [vehicleDetails, setVehicleDetails] = useState({});
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [stats, setStats] = useState({ online: 0, weak: 0, jammed: 0, lost: 0, offline: 0 });
  const [osrmSnappedCoords, setOsrmSnappedCoords] = useState({});
  const pendingRequestsRef = useRef({});
  const lastOsrmQueryTimeRef = useRef({}); // Throttle OSRM requests

  const rawTrailsRef = useRef({});
  const snappedTrailsRef = useRef({});
  const lastRawCoordsRef = useRef({}); // Cache for OSRM to prevent infinite loops
  const lastProcessedTelemetriesRef = useRef({});
  const lastSegmentsRef = useRef({});
  const triggeredGeofencesRef = useRef({}); // Stores { vehicleId: lastSegmentIdx }
  const lastRenderedPositionsRef = useRef({}); // Stores { vehicleId: { lat, lng, snappedLat, ... } }
  const prevStatusesRef = useRef({});
  const lastOnTrackSmsRef = useRef({}); // Tracks last on-track SMS time per vehicle (ms)

  // Tick state to force re-render when async OSRM calls resolve
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const updateRawTrail = (id, lat, lng) => {
    if (!rawTrailsRef.current[id]) {
      rawTrailsRef.current[id] = [];
    }
    const rawTrail = rawTrailsRef.current[id];
    const lastRaw = rawTrail[rawTrail.length - 1];
    if (!lastRaw) {
      rawTrail.push([lat, lng]);
      return;
    }
    const dist = haversine(lastRaw[0], lastRaw[1], lat, lng);
    if (dist >= 2) {
      rawTrail.push([lat, lng]);
      if (rawTrail.length > MAX_TRAIL) {
        rawTrailsRef.current[id] = rawTrail.slice(-MAX_TRAIL);
      }
    }
  };

  const updateOffRouteTrail = (id, snapLat, snapLng) => {
    if (!snappedTrailsRef.current[id]) {
      snappedTrailsRef.current[id] = [[snapLat, snapLng]];
      setTick(t => t + 1);
      return;
    }

    const snappedTrail = snappedTrailsRef.current[id];
    const lastSnap = snappedTrail[snappedTrail.length - 1];
    if (!lastSnap) {
      snappedTrail.push([snapLat, snapLng]);
      setTick(t => t + 1);
      return;
    }

    // Check movement
    const dist = haversine(lastSnap[0], lastSnap[1], snapLat, snapLng);
    if (dist < 2) return; // ignore tiny noise

    if (dist > 500) {
      // Large coordinate jump: do a straight link to avoid routing across the city
      snappedTrail.push([snapLat, snapLng]);
      if (snappedTrail.length > 300) {
        snappedTrailsRef.current[id] = snappedTrail.slice(-300);
      }
      setTick(t => t + 1);
      return;
    }



    // Fallback: simple point addition
    snappedTrail.push([snapLat, snapLng]);
    setTick(t => t + 1);
  };

  const fetchSnappedCoordinate = async (id, lat, lng) => {
    // Only query if the GPS coordinate has changed to prevent infinite loops & API spam
    const lastCoord = lastRawCoordsRef.current[id];
    if (lastCoord && lastCoord.lat === lat && lastCoord.lng === lng) {
      return;
    }
    lastRawCoordsRef.current[id] = { lat, lng };

    const now = Date.now();
    lastOsrmQueryTimeRef.current[id] = now;

    if (pendingRequestsRef.current[id]) return;
    pendingRequestsRef.current[id] = true;

    const GOOGLE_MAPS_API_KEY = import.meta.env.VITE_GOOGLE_MAPS_API_KEY;

    if (GOOGLE_MAPS_API_KEY) {
      try {
        // Google Roads API nearestRoads (via Vite/Netlify proxy)
        const url = `/api/v1/snapping/nearestRoads?points=${lat},${lng}&key=${GOOGLE_MAPS_API_KEY}`;
        const res = await fetch(url);
        if (res.ok) {
          const data = await res.json();
          if (data.snappedPoints && data.snappedPoints.length > 0) {
            const snapLat = data.snappedPoints[0].location.latitude;
            const snapLng = data.snappedPoints[0].location.longitude;
            // Compute snapped distance
            const distance = haversine(lat, lng, snapLat, snapLng);

            setOsrmSnappedCoords((prev) => ({
              ...prev,
              [id]: { lat: snapLat, lng: snapLng, distance }
            }));
            console.log(`[Google Maps] Successfully snapped ${id} coordinates.`);
            delete pendingRequestsRef.current[id];
            return;
          }
        }
      } catch (e) {
        console.warn(`[Google Maps] Snapping failed for ${id}, falling back to OSRM:`, e);
      }
    }

    // Fallback to free public OSRM API for snapping to the nearest road
    try {
      const url = `https://router.project-osrm.org/nearest/v1/driving/${lng},${lat}?number=1`;
      const res = await fetch(url);

      if (res.ok) {
        const data = await res.json();
        if (data.code === "Ok" && data.waypoints && data.waypoints.length > 0) {
          // OSRM returns coordinates as [longitude, latitude]
          const snapLng = data.waypoints[0].location[0];
          const snapLat = data.waypoints[0].location[1];
          const distance = data.waypoints[0].distance; // Distance in meters from original point

          setOsrmSnappedCoords((prev) => ({
            ...prev,
            [id]: { lat: snapLat, lng: snapLng, distance }
          }));
          console.log(`[OSRM] Successfully snapped ${id} coordinates.`);
        }
      }
    } catch (e) {
      console.warn(`[OSRM] Snapping failed for ${id}:`, e);
    } finally {
      delete pendingRequestsRef.current[id];
    }
  };

  // Telemetry stream processing
  useEffect(() => {
    Object.entries(rawVehicles).forEach(([id, data]) => {
      if (!id) return;
      if (!data || typeof data.lat !== "number" || typeof data.lng !== "number") return;

      // Use filtered/locked coordinates as primary active coordinates
      const activeLat = data.lat;
      const activeLng = data.lng;

      const lastProcessed = lastProcessedTelemetriesRef.current[id];
      const hasNewTelemetry = !lastProcessed ||
        lastProcessed.lat !== activeLat ||
        lastProcessed.lng !== activeLng ||
        lastProcessed.timestamp !== data.timestamp;

      const snappingToggled = lastProcessed && lastProcessed.useSnapping !== useSnapping;

      if (hasNewTelemetry || snappingToggled || (!lastProcessed && useSnapping)) {
        if (useSnapping) {
          fetchSnappedCoordinate(id, activeLat, activeLng);
        }
      }

      if (!hasNewTelemetry && !snappingToggled) return;
      lastProcessedTelemetriesRef.current[id] = {
        lat: activeLat,
        lng: activeLng,
        timestamp: data.timestamp,
        useSnapping
      };

      // Motion lock: Skip appending points to trails if the vehicle is stationary (prevents drift artifacts)
      const lastRendered = lastRenderedPositionsRef.current[id];
      const distFromLastRendered = (lastRendered && typeof lastRendered.lat === "number")
        ? haversine(lastRendered.lat, lastRendered.lng, activeLat, activeLng)
        : Infinity;
      const speed = data.speed_kmh ?? data.speed ?? 0;

      const hasMpu = data.is_moving !== undefined && data.is_moving !== null;
      const isMpuMoving = hasMpu && (data.is_moving === true || data.is_moving === "true" || data.is_moving === 1 || data.is_moving === "1" || String(data.is_moving).toLowerCase() === "true");
      const isStationary = hasMpu 
        ? (!isMpuMoving || (speed <= 3.0 && distFromLastRendered < 15)) 
        : (speed <= 3.0 && distFromLastRendered < 15);

      if (isStationary) {
        return;
      }

      // 1. Update raw trail
      updateRawTrail(id, activeLat, activeLng);

      // 2. Update snapped trail
      const lastSeg = lastSegmentsRef.current[id] !== undefined ? lastSegmentsRef.current[id] : null;
      const snap = snapToRoute(activeLat, activeLng, snappingRoute, lastSeg);
      if (!snap.offRoute) {
        lastSegmentsRef.current[id] = snap.segmentIdx;
        const snapLat = snap.snappedLat;
        const snapLng = snap.snappedLng;

        if (!snappedTrailsRef.current[id]) {
          snappedTrailsRef.current[id] = [[snapLat, snapLng]];
          setTick(t => t + 1);
        } else {
          const snappedTrail = snappedTrailsRef.current[id];
          const lastSnap = snappedTrail[snappedTrail.length - 1];
          if (lastSnap) {
            const dist = haversine(lastSnap[0], lastSnap[1], snapLat, snapLng);
            if (dist >= 2) {
              if (dist > 500) {
                snappedTrail.push([snapLat, snapLng]);
                if (snappedTrail.length > 300) {
                  snappedTrailsRef.current[id] = snappedTrail.slice(-300);
                }
                setTick(t => t + 1);
              } else {
                const lastOnRoute = isPointOnRoute(snappingRoute, lastSnap);
                if (lastOnRoute) {
                  // Both points on official route: slice the route geometry
                  const roadCoords = sliceRoadRoute(snappingRoute, lastSnap, [snapLat, snapLng]);
                  if (roadCoords && roadCoords.length > 0) {
                    snappedTrail.push(...roadCoords);
                  } else {
                    snappedTrail.push([snapLat, snapLng]);
                  }
                  if (snappedTrail.length > 300) {
                    snappedTrailsRef.current[id] = snappedTrail.slice(-300);
                  }
                  setTick(t => t + 1);
                } else {
                  // Transitioning from off-route to on-route
                  updateOffRouteTrail(id, snapLat, snapLng);
                }
              }
            }
          } else {
            snappedTrail.push([snapLat, snapLng]);
            setTick(t => t + 1);
          }
        }
      }
    });
  }, [rawVehicles, snappingRoute, useSnapping]);

  useEffect(() => {
    let active = true;
    const fetchVehicles = async () => {
      try {
        const res = await fetch("/api/v1/tracking/vehicles");
        if (!res.ok) throw new Error("Failed to fetch vehicles from MongoDB");
        const data = await res.json();
        if (!active) return;
        
        const raw = data.vehicles || {};
        const mapped = {};
        Object.entries(raw).forEach(([dbId, item]) => {
          const displayId = (DEVICE_TO_DISPLAY_MAP[dbId] || dbId).trim();
          if (item) {
            const existing = mapped[displayId];
            // If duplicate display IDs exist, keep the one with the newest timestamp
            if (!existing || (item.timestamp && (!existing.timestamp || item.timestamp > existing.timestamp))) {
              mapped[displayId] = {
                ...item,
                truck_id: displayId,
                vehicle_id: displayId
              };
            }
          }
        });
        
        setRawVehicles(mapped);
        setVehicleDetails(data.vehicle_details || {});
        setLoading(false);
        setError(null);
      } catch (err) {
        console.error("MongoDB fetch failed:", err);
        if (active) {
          setError(err.message || String(err));
          setLoading(false);
        }
      }
    };

    fetchVehicles();
    const interval = setInterval(fetchVehicles, 2000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  // Compute enriched vehicle map
  const vehicles = useMemo(() => {
    const enriched = {};
    const firebaseIds = Object.keys(rawVehicles);
    const idsToShow = [...new Set([...ACTIVE_VEHICLE_IDS, ...firebaseIds])];

    const getTrail = (id) => {
      if (useSnapping) {
        return snappedTrailsRef.current[id] || [];
      } else {
        return rawTrailsRef.current[id] || [];
      }
    };

    // Build enriched vehicle list
    idsToShow.forEach((id, idx) => {
      const data = rawVehicles[id] || null;
      const details = vehicleDetails[id] || {};
      const display_name = details.display_name || null;
      const cached = loadLastLocation(id);

      const status = getStatus(data);
      const online = status !== "offline" && status !== "lost";

      // Robust MPU-based movement tracking
      const lastRendered = lastRenderedPositionsRef.current[id];
      const distFromLastRendered = (lastRendered && typeof lastRendered.lat === "number" && data)
        ? haversine(lastRendered.lat, lastRendered.lng, data.lat, data.lng)
        : Infinity;
      const speed = data ? (data.speed_kmh ?? data.speed ?? 0) : 0;

      const hasMpu = data && data.is_moving !== undefined && data.is_moving !== null;
      const isMpuMoving = hasMpu && (data.is_moving === true || data.is_moving === "true" || data.is_moving === 1 || data.is_moving === "1" || String(data.is_moving).toLowerCase() === "true");
      const isStationary = hasMpu 
        ? (!isMpuMoving || (speed <= 3.0 && distFromLastRendered < 15)) 
        : (speed <= 3.0 && distFromLastRendered < 15);
      const isEnrichedMoving = data ? !isStationary : false;

      let lat = null;
      let lng = null;
      let snappedLat = null;
      let snappedLng = null;
      let segmentIdx = null;
      let offRoute = false;
      let distanceMeters = 0;
      let routeMeters = 0;
      let driftError = null;

      const hasActiveCoords = data && typeof data.lat === "number" && typeof data.lng === "number";
      if (hasActiveCoords) {
        let activeLat = data.lat;
        let activeLng = data.lng;

        if (isStationary && lastRendered && typeof lastRendered.rawLat === "number") {
          activeLat = lastRendered.rawLat;
          activeLng = lastRendered.rawLng;
        }

        lat = activeLat;
        lng = activeLng;

        const lastSeg = lastSegmentsRef.current[id] !== undefined ? lastSegmentsRef.current[id] : null;
        const snap = snapToRoute(activeLat, activeLng, snappingRoute, lastSeg);

        snappedLat = snap.snappedLat;
        snappedLng = snap.snappedLng;
        segmentIdx = snap.segmentIdx;
        offRoute = snap.offRoute;
        distanceMeters = snap.distanceMeters;
        routeMeters = snap.routeMeters || 0;
        driftError = (useSnapping && !offRoute) ? Math.round(distanceMeters) : Math.round((data.hdop || 1.5) * 3.5);

          if (useSnapping) {
            let bestSnapCandidate = null;
            // UNIVERSAL SYNCHRONOUS SNAPPING
            // 1. Closest Road (from OSRM / Google Roads API) if within 5m, or within 35m and closer than the Yatra route
            const osrmSnap = osrmSnappedCoords[id];
            if (osrmSnap && (
              osrmSnap.distance <= 5 ||
              (osrmSnap.distance <= 35 && (!snap || osrmSnap.distance < snap.distanceMeters))
            )) {
              bestSnapCandidate = {
                lat: osrmSnap.lat,
                lng: osrmSnap.lng,
                distance: osrmSnap.distance,
                segmentIdx: null,
                offRoute: snap ? snap.distanceMeters > 15 : true,
                type: "osrm"
              };
            }
            // 2. Yatra Route (Main Road) within 35 meters
            else if (snap && snap.distanceMeters <= 35) {
              bestSnapCandidate = {
                lat: snap.snappedLat,
                lng: snap.snappedLng,
                distance: snap.distanceMeters,
                segmentIdx: snap.segmentIdx,
                offRoute: snap.offRoute,
                type: "yatra"
              };
            }
            // 3. Fallback to OSRM Nearest API Map Matching (up to 35 meters)
            else if (osrmSnap && osrmSnap.distance <= 35) {
              bestSnapCandidate = {
                lat: osrmSnap.lat,
                lng: osrmSnap.lng,
                distance: osrmSnap.distance,
                segmentIdx: null,
                offRoute: true,
                type: "osrm"
              };
            }
            // 4. Fallback to Yatra Route (only if close, up to 35m, otherwise do not snap)
            else if (snap && snap.distanceMeters <= 35) {
              bestSnapCandidate = {
                lat: snap.snappedLat,
                lng: snap.snappedLng,
                distance: snap.distanceMeters,
                segmentIdx: snap.segmentIdx,
                offRoute: true,
                type: "yatra"
              };
            }

            if (bestSnapCandidate) {
              lat = bestSnapCandidate.lat;
              lng = bestSnapCandidate.lng;
              snappedLat = bestSnapCandidate.lat;
              snappedLng = bestSnapCandidate.lng;
              segmentIdx = bestSnapCandidate.segmentIdx;
              offRoute = bestSnapCandidate.offRoute;
              distanceMeters = bestSnapCandidate.distance;
              driftError = Math.round(distanceMeters);

              if (bestSnapCandidate.type === "yatra") {
                lastSegmentsRef.current[id] = segmentIdx;
              } else {
                lastSegmentsRef.current[id] = null;
              }
            } else {
              lastSegmentsRef.current[id] = null;
            }
          } else {
            lastSegmentsRef.current[id] = null;
          }

        // Save the rendered position for next ticks and localStorage persistence
        const renderedState = {
          lat,
          lng,
          rawLat: activeLat,
          rawLng: activeLng,
          snappedLat,
          snappedLng,
          segmentIdx,
          offRoute,
          distanceMeters,
          routeMeters,
          driftError,
          bearing: data.bearing || 0,
          speed: data.speed || 0,
          satellites: data.satellites || 0,
          hdop: data.hdop || 0,
          timestamp: data.timestamp || new Date().toISOString(),
          is_estimated: data.is_estimated || false,
          is_moving: data.is_moving || false
        };

        lastRenderedPositionsRef.current[id] = renderedState;

        try {
          localStorage.setItem(`last_loc_${id}`, JSON.stringify(renderedState));
        } catch (e) {
          console.error(`Error saving location for ${id} to localStorage:`, e);
        }
      }
      else if (cached) {
        // Fallback to cached location
        lat = cached.lat;
        lng = cached.lng;
        snappedLat = cached.snappedLat;
        snappedLng = cached.snappedLng;
        segmentIdx = cached.segmentIdx;
        offRoute = cached.offRoute;
        distanceMeters = cached.distanceMeters;
        routeMeters = cached.routeMeters || 0;
        driftError = cached.driftError;
      }

      if (data) {
        enriched[id] = {
          ...data,
          display_name,
          key: id,
          status,
          online,
          rawLat: hasActiveCoords ? (data.rawLat || data.lat) : (cached ? cached.rawLat : null),
          rawLng: hasActiveCoords ? (data.rawLng || data.lng) : (cached ? cached.rawLng : null),
          lat,
          lng,
          snappedLat,
          snappedLng,
          segmentIdx,
          offRoute,
          distanceMeters,
          routeMeters,
          driftError,
          bearing: hasActiveCoords ? (data.bearing || 0) : (cached ? (cached.bearing || 0) : (data.bearing || 0)),
          speed: hasActiveCoords ? (data.speed || 0) : (cached ? (cached.speed || 0) : (data.speed || 0)),
          satellites: hasActiveCoords ? (data.satellites || 0) : (cached ? (cached.satellites || 0) : (data.satellites || 0)),
          hdop: hasActiveCoords ? (data.hdop || 0) : (cached ? (cached.hdop || 0) : (data.hdop || 0)),
          is_moving: hasActiveCoords ? isEnrichedMoving : (cached ? (cached.is_moving || false) : false),
          isCalibrated: false,
          useCalibrated: false,
          calibratedLat: null,
          calibratedLng: null,
          orderIdx: idx,
          trail: [...getTrail(id)],
          rawTrail: [...(rawTrailsRef.current[id] || [])]
        };
      } else {
        enriched[id] = {
          display_name,
          key: id,
          status: "offline",
          online: false,
          rawLat: cached ? cached.rawLat : null,
          rawLng: cached ? cached.rawLng : null,
          lat,
          lng,
          snappedLat,
          snappedLng,
          segmentIdx,
          offRoute,
          distanceMeters,
          driftError,
          bearing: cached ? (cached.bearing || 0) : 0,
          speed: cached ? (cached.speed || 0) : 0,
          satellites: cached ? (cached.satellites || 0) : 0,
          hdop: cached ? (cached.hdop || 0) : 0,
          is_moving: cached ? (cached.is_moving || false) : false,
          is_estimated: cached ? (cached.is_estimated || false) : false,
          timestamp: cached ? cached.timestamp : null,
          isCalibrated: false,
          useCalibrated: false,
          orderIdx: idx,
          trail: [...getTrail(id)],
          rawTrail: [...(rawTrailsRef.current[id] || [])]
        };
      }
    });

    return enriched;
    // eslint-disable-next-line react-hooks/exhaustive-deps, react-hooks/refs
  }, [rawVehicles, vehicleDetails, tick, snappingRoute, useSnapping, osrmSnappedCoords]);

  // Compute stats and spacing alerts
  useEffect(() => {
    const s = { online: 0, weak: 0, jammed: 0, lost: 0, offline: 0 };
    Object.values(vehicles).forEach((v) => { if (s[v.status] !== undefined) s[v.status]++; });
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setStats(s);

    // Track status transitions to write to persistent Firebase /logs
    Object.entries(vehicles).forEach(([id, v]) => {
      const prevStatus = prevStatusesRef.current[id];
      const newStatus = v.status;
      
      if (prevStatus && prevStatus !== newStatus) {
        let logMsg = "";
        let severity = "normal";
        let type = "SIGNAL";
        
        if (prevStatus === "online" && (newStatus === "lost" || newStatus === "offline")) {
          logMsg = `${id} — GPS signal lost.`;
          severity = "warning";
        } else if ((prevStatus === "lost" || prevStatus === "offline") && newStatus === "online") {
          logMsg = `${id} — GPS signal recovered.`;
          severity = "normal";
        } else if (newStatus === "jammed") {
          logMsg = `${id} — GPS jamming detected. Last known position shown.`;
          severity = "critical";
          type = "JAMMING";
        }
        
        if (logMsg) {
          try {
            const logTime = Date.now();
            const logRef = ref(db, `logs/${logTime}`);
            set(logRef, {
              timestamp: new Date().toISOString(),
              type,
              severity,
              message: logMsg,
              vehicleId: id
            });
          } catch (err) {
            console.error("Failed to write status log to Firebase:", err);
          }
        }
      }
      prevStatusesRef.current[id] = newStatus;
    });

    const positioned = Object.values(vehicles)
      .filter((v) => v.lat && v.lng)
      .sort((a, b) => (a.orderIdx || 0) - (b.orderIdx || 0));

    const newAlerts = [];
    for (let i = 0; i < positioned.length - 1; i++) {
      const a = positioned[i], b = positioned[i + 1];
      const dist = haversine(a.lat, a.lng, b.lat, b.lng);
      if (dist < ALERT.TOO_CLOSE_METERS) {
        newAlerts.push({
          id: `close_${a.key}_${b.key}`,
          type: "TOO_CLOSE",
          severity: "critical",
          message: `${a.key} and ${b.key} are only ${Math.round(dist)}m apart`,
          trucks: [a.key, b.key],
          time: new Date(),
        });
      } else if (dist > ALERT.GAP_LARGE_METERS) {
        newAlerts.push({
          id: `gap_${a.key}_${b.key}`,
          type: "GAP_LARGE",
          severity: "warning",
          message: `Gap between ${a.key} and ${b.key} is ${Math.round(dist)}m`,
          trucks: [a.key, b.key],
          time: new Date(),
        });
      }
    }

    Object.values(vehicles)
      .filter((v) => v.is_jammed)
      .forEach((v) => {
        newAlerts.push({
          id: `jammed_${v.key}`,
          type: "JAMMED",
          severity: "critical",
          message: `${v.key} — GPS jamming detected. Last known position shown.`,
          trucks: [v.key],
          time: new Date(),
        });
      });

    // ── Helper: push a message to Firebase SMS queue ─────────────────────────
    const sendSmsToFirebase = (phone, message, vehicleId, lat, lng, type) => {
      const msgId = `msg_${type}_${vehicleId}_${Date.now()}`;
      const queueUrl = `https://aidatasave-adfe6-default-rtdb.firebaseio.com/sms_queue/${msgId}.json`;
      fetch(queueUrl, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          to: phone,
          message,
          status: "pending",
          lat,
          lng,
          timestamp: new Date().toISOString()
        })
      })
        .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
        .then(d => console.log(`[SMS] ${type} queued for ${vehicleId}:`, d))
        .catch(e => console.error(`[SMS] ${type} queue failed for ${vehicleId}:`, e));
    };

    // Off-Route Alert Trigger (SMS Alert when device is online but out of range/off-route)
    Object.values(vehicles).forEach(v => {
      if (!v.lat || !v.lng) return;
      const id = v.key;

      const isActive = v.lat && v.lng && v.status !== "offline" && v.status !== "lost";
      const isOffRouteAlert = isActive && v.distanceMeters > ALERT.OFF_ROUTE_METERS;
      const offRouteKey = `off_route_${id}`;
      let prevOffRoute = false;
      try {
        const cachedGeoOff = localStorage.getItem(offRouteKey);
        prevOffRoute = cachedGeoOff === "true";
      } catch (err) {
        console.error("Failed to load off-route state:", err);
      }

      if (isOffRouteAlert && !prevOffRoute) {
        try {
          localStorage.setItem(offRouteKey, "true");
        } catch (err) {
          console.error("Failed to save off-route state:", err);
        }

        let phone = TRUCK_PHONES[id] || "8469091377";
        if (phone && !phone.startsWith("+")) phone = "+91" + phone;

        if (phone) {
          const message = "the truck is outside track ";
          console.log(`[SMS Queue] Triggering off-route SMS for ${id}`);
          sendSmsToFirebase(phone, message, id, v.lat, v.lng, "off_route");

          // Write persistent log to Firebase
          try {
            const logTime = Date.now();
            const logRef = ref(db, `logs/${logTime}`);
            set(logRef, {
              timestamp: new Date().toISOString(),
              type: "OFF_ROUTE",
              severity: "critical",
              message: `${id} went off-route (${Math.round(v.distanceMeters)}m). SMS queued to ${phone}.`,
              vehicleId: id
            });
          } catch (logErr) {
            console.error("Failed to write off-route log to Firebase:", logErr);
          }
        }
      } else if (!isOffRouteAlert && prevOffRoute) {
        try {
          localStorage.setItem(offRouteKey, "false");
        } catch (err) {
          console.error("Failed to save off-route state:", err);
        }

        // Write persistent recovery log to Firebase
        try {
          const logTime = Date.now();
          const logRef = ref(db, `logs/${logTime}`);
          set(logRef, {
            timestamp: new Date().toISOString(),
            type: "ON_ROUTE",
            severity: "normal",
            message: `${id} returned to the route.`,
            vehicleId: id
          });
        } catch (logErr) {
          console.error("Failed to write on-route log to Firebase:", logErr);
        }
      }

      // ── Every-60-second "on track" heartbeat SMS ────────────────────────────
      if (!isOffRouteAlert && isActive) {
        const now = Date.now();
        const lastOnTrack = lastOnTrackSmsRef.current[id] || 0;
        const ONE_MIN_MS = 60_000;
        if (now - lastOnTrack >= ONE_MIN_MS) {
          lastOnTrackSmsRef.current[id] = now;
          let phone = TRUCK_PHONES[id] || "8469091377";
          if (phone && !phone.startsWith("+")) phone = "+91" + phone;
          sendSmsToFirebase(phone, "the truck is on track ", id, v.lat, v.lng, "on_track");
          console.log(`[SMS] On-track heartbeat sent for ${id}`);
        }
      }

      if (isOffRouteAlert) {
        let phone = TRUCK_PHONES[id] || "8469091377";
        newAlerts.push({
          id: `off_route_${id}`,
          type: "OFF_ROUTE",
          severity: "critical",
          message: `${id} is off-route (${Math.round(v.distanceMeters)}m)! SMS sent to ${phone}.`,
          trucks: [id],
          time: new Date()
        });
      }
    });

    Object.values(vehicles)
      .filter((v) => v.status === "lost")
      .forEach((v) => {
        newAlerts.push({
          id: `lost_${v.key}`,
          type: "SIGNAL_LOST",
          severity: "warning",
          message: `${v.key} — No signal for over ${ALERT.SIGNAL_LOST_SEC} seconds`,
          trucks: [v.key],
          time: new Date(),
        });
      });

    setAlerts(newAlerts);
  }, [vehicles]);

  const refreshed = useMemo(() => {
    const r = {};
    Object.entries(vehicles).forEach(([id, v]) => {
      r[id] = { ...v, status: getStatus(v) };
    });
    return r;
  }, [vehicles]);

  return { vehicles: refreshed, alerts, loading, stats, error };
}


