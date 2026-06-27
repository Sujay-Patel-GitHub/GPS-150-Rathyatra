// src/utils/routeSnap.js
// Client-side route snapping utility.
// Snaps a GPS coordinate to the nearest point on the Rath Yatra route polyline.
// Uses the same math as PostGIS ST_ClosestPoint — pure JavaScript, no server needed.

import { YATRA_ROUTE } from "../lib/constants";

const DEG_TO_RAD = Math.PI / 180;
const EARTH_R    = 6371000; // metres

// ── Haversine distance (metres) ──────────────────────────────────────────────
export function haversine(lat1, lng1, lat2, lng2) {
  const dLat = (lat2 - lat1) * DEG_TO_RAD;
  const dLng = (lng2 - lng1) * DEG_TO_RAD;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * DEG_TO_RAD) *
    Math.cos(lat2 * DEG_TO_RAD) *
    Math.sin(dLng / 2) ** 2;
  return EARTH_R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// ── Project point P onto segment AB, return closest point on segment ─────────
// All coordinates in decimal degrees. Returns { lat, lng, t } where t ∈ [0,1].
function closestPointOnSegment(pLat, pLng, aLat, aLng, bLat, bLng) {
  // Convert to flat-earth metres relative to A (good enough at city scale)
  const cosLat = Math.cos(((aLat + bLat) / 2) * DEG_TO_RAD);

  const ax = 0, ay = 0;
  const bx = (bLng - aLng) * DEG_TO_RAD * EARTH_R * cosLat;
  const by = (bLat - aLat) * DEG_TO_RAD * EARTH_R;
  const px = (pLng - aLng) * DEG_TO_RAD * EARTH_R * cosLat;
  const py = (pLat - aLat) * DEG_TO_RAD * EARTH_R;

  const dx = bx - ax, dy = by - ay;
  const lenSq = dx * dx + dy * dy;

  let t = 0;
  if (lenSq > 0) {
    t = ((px - ax) * dx + (py - ay) * dy) / lenSq;
    t = Math.max(0, Math.min(1, t)); // clamp to segment
  }

  // Convert back to lat/lng
  const snapX = ax + t * dx;
  const snapY = ay + t * dy;

  const snapLat = aLat + (snapY / EARTH_R) * (180 / Math.PI);
  const snapLng = aLng + (snapX / (EARTH_R * cosLat)) * (180 / Math.PI);

  return { lat: snapLat, lng: snapLng, t };
}

// ── Snap a GPS point to the Rath Yatra route ─────────────────────────────────
// Returns:
//   snappedLat      — snapped latitude
//   snappedLng      — snapped longitude
//   distanceMeters  — perpendicular distance from truck to route (accuracy indicator)
//   routeMeters     — distance along route from start to snapped point (order metric)
//   segmentIdx      — which route segment the truck is nearest to
//   offRoute        — true if truck is > OFF_ROUTE_THRESHOLD metres from route
//
const OFF_ROUTE_THRESHOLD = 15; // metres

export function snapToRoute(lat, lng, route = YATRA_ROUTE, lastSegmentIdx = null) {
  if (!route || route.length < 2) {
    return {
      snappedLat:     lat,
      snappedLng:     lng,
      distanceMeters: 0,
      routeMeters:    0,
      segmentIdx:     0,
      offRoute:       false,
    };
  }

  // Pre-compute cumulative distances along route
  const cumDist = [0];
  for (let i = 1; i < route.length; i++) {
    cumDist.push(
      cumDist[i - 1] + haversine(
        route[i - 1][0], route[i - 1][1],
        route[i][0],     route[i][1]
      )
    );
  }

  let bestDist = Infinity;
  let bestSnap = { lat, lng, t: 0 };
  let bestSeg  = 0;
  let foundLocal = false;

  // 1. Try local hysteresis search if lastSegmentIdx is provided
  if (lastSegmentIdx !== null && lastSegmentIdx >= 0 && lastSegmentIdx < route.length - 1) {
    const startRange = Math.max(0, lastSegmentIdx - 1);
    const endRange = Math.min(route.length - 2, lastSegmentIdx + 2);

    for (let i = startRange; i <= endRange; i++) {
      const snap = closestPointOnSegment(
        lat, lng,
        route[i][0],     route[i][1],
        route[i + 1][0], route[i + 1][1]
      );
      const dist = haversine(lat, lng, snap.lat, snap.lng);
      if (dist < bestDist) {
        bestDist = dist;
        bestSnap = snap;
        bestSeg  = i;
      }
    }

    // Accept local segment if it is within 35 meters
    if (bestDist <= 35) {
      foundLocal = true;
    }
  }

  // 2. Fallback to global scan if not found locally
  if (!foundLocal) {
    bestDist = Infinity;
    for (let i = 0; i < route.length - 1; i++) {
      const snap = closestPointOnSegment(
        lat, lng,
        route[i][0],     route[i][1],
        route[i + 1][0], route[i + 1][1]
      );
      const dist = haversine(lat, lng, snap.lat, snap.lng);
      if (dist < bestDist) {
        bestDist = dist;
        bestSnap = snap;
        bestSeg  = i;
      }
    }
  }

  const isOffRoute = bestDist > OFF_ROUTE_THRESHOLD;

  // 3. Exact Snapping to Route Centerline
  const snappedLat = bestSnap.lat;
  const snappedLng = bestSnap.lng;

  // Distance along route = cumulative distance to start of best segment
  //                        + fractional distance within segment
  const segLen      = haversine(
    route[bestSeg][0],     route[bestSeg][1],
    route[bestSeg + 1][0], route[bestSeg + 1][1]
  );
  const routeMeters = cumDist[bestSeg] + bestSnap.t * segLen;

  return {
    snappedLat,
    snappedLng,
    distanceMeters: Math.round(bestDist),
    routeMeters:    Math.round(routeMeters),
    segmentIdx:     bestSeg,
    offRoute:       isOffRoute,
  };
}

// ── Compute order of all trucks along the route ───────────────────────────────
// Input: vehicles object { id: { lat, lng, ... } }
// Output: array sorted by routeMeters descending (front of procession = index 0)
export function computeRouteOrder(vehicles) {
  return Object.entries(vehicles)
    .filter(([, v]) => v && typeof v.lat === "number" && typeof v.lng === "number")
    .map(([id, v]) => {
      const snap = snapToRoute(v.lat, v.lng);
      return {
        id,
        ...v,
        snappedLat:     snap.snappedLat,
        snappedLng:     snap.snappedLng,
        distanceMeters: snap.distanceMeters,
        routeMeters:    snap.routeMeters,
        offRoute:       snap.offRoute,
      };
    })
    .sort((a, b) => b.routeMeters - a.routeMeters) // front first
    .map((v, idx) => ({ ...v, routeOrder: idx + 1 }));
}

// ── Fetch high-density road-following route from OSRM ─────────────────────────
export async function fetchRoadRoute(waypoints = YATRA_ROUTE) {
  try {
    const coordsStr = waypoints.map(wp => `${wp[1]},${wp[0]}`).join(";");
    const url = `https://router.project-osrm.org/route/v1/foot/${coordsStr}?overview=full&geometries=geojson`;
    
    const res = await fetch(url);
    if (!res.ok) throw new Error("OSRM API response error");
    
    const data = await res.json();
    if (data.code !== "Ok" || !data.routes || data.routes.length === 0) {
      throw new Error("No route found in OSRM");
    }
    
    // Convert GeoJSON [lng, lat] coordinate array to Leaflet [lat, lng] array
    const roadCoords = data.routes[0].geometry.coordinates.map(c => [c[1], c[0]]);
    console.log(`[OSRM] Successfully expanded ${waypoints.length} waypoints to ${roadCoords.length} high-density road nodes.`);
    return roadCoords;
  } catch (e) {
    console.warn("[OSRM] Failed to fetch high-density road geometry. Snapping to sparse waypoints instead:", e);
    return waypoints;
  }
}

// ── Gap between consecutive trucks in the procession ─────────────────────────
// Returns array of { frontId, backId, gapMeters, tooClose, gapTooLarge }
export function computeSpacing(orderedTrucks, tooCloseM = 25, gapLargeM = 200) {
  const gaps = [];
  for (let i = 0; i < orderedTrucks.length - 1; i++) {
    const front = orderedTrucks[i];
    const back  = orderedTrucks[i + 1];
    const gap   = front.routeMeters - back.routeMeters;
    gaps.push({
      frontId:     front.id,
      backId:      back.id,
      gapMeters:   Math.max(0, gap),
      tooClose:    gap < tooCloseM,
      gapTooLarge: gap > gapLargeM,
    });
  }
  return gaps;
}

// ── Decode Google Maps Polyline ──────────────────────────────────────────────
export function decodePolyline(encoded) {
  const poly = [];
  let index = 0, len = encoded.length;
  let lat = 0, lng = 0;

  while (index < len) {
    let b, shift = 0, result = 0;
    do {
      b = encoded.charCodeAt(index++) - 63;
      result |= (b & 0x1f) << shift;
      shift += 5;
    } while (b >= 0x20);
    const dlat = ((result & 1) ? ~(result >> 1) : (result >> 1));
    lat += dlat;

    shift = 0;
    result = 0;
    do {
      b = encoded.charCodeAt(index++) - 63;
      result |= (b & 0x1f) << shift;
      shift += 5;
    } while (b >= 0x20);
    const dlng = ((result & 1) ? ~(result >> 1) : (result >> 1));
    lng += dlng;

    poly.push([lat / 1e5, lng / 1e5]);
  }
  return poly;
}

// ── Fetch true road distance between two trucks (Google Maps with OSRM fallback) ──
export async function fetchLiveDistanceRoute(lat1, lng1, lat2, lng2) {
  const GOOGLE_MAPS_API_KEY = import.meta.env.VITE_GOOGLE_MAPS_API_KEY;

  if (GOOGLE_MAPS_API_KEY) {
    try {
      // Google Directions API URL (via Vite proxy to bypass CORS and Adblockers)
      const url = `/api/v1/routing?origin=${lat1},${lng1}&destination=${lat2},${lng2}&key=${GOOGLE_MAPS_API_KEY}`;
      
      const res = await fetch(url);
      if (!res.ok) throw new Error("Google Maps API response error");
      
      const data = await res.json();
      if (data.status !== "OK" || !data.routes || data.routes.length === 0) {
        if (data.status === "ZERO_RESULTS") return null;
        throw new Error(`Google Maps API error: ${data.status}`);
      }
      
      const route = data.routes[0];
      const leg = route.legs[0];
      const distanceMeters = leg.distance.value; 
      
      // decode the geometry into an array of Leaflet [lat, lng] points
      const roadCoords = decodePolyline(route.overview_polyline.points);
      
      console.log("[Google Maps] Successfully fetched live distance route.");
      return { distanceMeters, geometry: roadCoords };
    } catch (e) {
      console.warn("[Google Maps] Failed to fetch distance route, falling back to OSRM:", e);
    }
  }

  // Fallback to OSRM Routing
  try {
    // Public OSRM routing expects longitude,latitude format
    const url = `https://router.project-osrm.org/route/v1/driving/${lng1},${lat1};${lng2},${lat2}?overview=full&geometries=geojson`;
    
    const res = await fetch(url);
    if (!res.ok) throw new Error("OSRM routing API response error");
    
    const data = await res.json();
    if (data.code !== "Ok" || !data.routes || data.routes.length === 0) {
      throw new Error(`OSRM API error: ${data.code}`);
    }
    
    const route = data.routes[0];
    const distanceMeters = route.distance; // exact distance in meters
    
    // convert OSRM [lng, lat] coordinate array to Leaflet [lat, lng] array
    const roadCoords = route.geometry.coordinates.map(c => [c[1], c[0]]);
    
    console.log("[OSRM] Successfully fetched live distance route.");
    return { distanceMeters, geometry: roadCoords };
  } catch (e) {
    console.warn("[OSRM] Failed to fetch distance route between trucks:", e);
    return null;
  }
}


