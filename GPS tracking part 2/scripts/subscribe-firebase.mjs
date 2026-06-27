import { loadEnv } from "./load_env.js";
loadEnv();

import { initializeApp } from "firebase/app";
import { getDatabase, ref, onValue, off } from "firebase/database";

const firebaseConfig = {
  apiKey: process.env.VITE_FIREBASE_API_KEY || "YOUR_FIREBASE_API_KEY",
  authDomain: process.env.VITE_FIREBASE_AUTH_DOMAIN || "YOUR_FIREBASE_AUTH_DOMAIN",
  databaseURL: process.env.VITE_FIREBASE_DATABASE_URL || "YOUR_FIREBASE_DATABASE_URL",
  projectId: process.env.VITE_FIREBASE_PROJECT_ID || "YOUR_FIREBASE_PROJECT_ID",
  storageBucket: process.env.VITE_FIREBASE_STORAGE_BUCKET || "YOUR_FIREBASE_STORAGE_BUCKET",
  messagingSenderId: process.env.VITE_FIREBASE_MESSAGING_SENDER_ID || "YOUR_FIREBASE_MESSAGING_SENDER_ID",
  appId: process.env.VITE_FIREBASE_APP_ID || "YOUR_FIREBASE_APP_ID",
};

const app = initializeApp(firebaseConfig);
const db = getDatabase(app);

const LOCAL_ROADS = [
  {
    name: "Polytechnic Campus Rd",
    geometry: [
      [23.02562, 72.54529],
      [23.02598, 72.54563],
      [23.02640, 72.54604],
      [23.02660, 72.54623],
      [23.026792, 72.54642],
      [23.026872, 72.546507],
      [23.02720, 72.54682],
      [23.02758, 72.54709]
    ]
  }
];

const DEG_TO_RAD = Math.PI / 180;
const EARTH_R    = 6371000;

function haversine(lat1, lng1, lat2, lng2) {
  const dLat = (lat2 - lat1) * DEG_TO_RAD;
  const dLng = (lng2 - lng1) * DEG_TO_RAD;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * DEG_TO_RAD) *
    Math.cos(lat2 * DEG_TO_RAD) *
    Math.sin(dLng / 2) ** 2;
  return EARTH_R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function closestPointOnSegment(pLat, pLng, aLat, aLng, bLat, bLng) {
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
    t = Math.max(0, Math.min(1, t));
  }

  const snapX = ax + t * dx;
  const snapY = ay + t * dy;

  const snapLat = aLat + (snapY / EARTH_R) * (180 / Math.PI);
  const snapLng = aLng + (snapX / (EARTH_R * cosLat)) * (180 / Math.PI);

  return { lat: snapLat, lng: snapLng, t };
}

function snapToRoute(lat, lng, route) {
  if (!route || route.length < 2) return { snappedLat: lat, snappedLng: lng, distanceMeters: 0 };
  let bestDist = Infinity;
  let bestSnap = null;
  for (let i = 0; i < route.length - 1; i++) {
    const snap = closestPointOnSegment(lat, lng, route[i][0], route[i][1], route[i+1][0], route[i+1][1]);
    const dist = haversine(lat, lng, snap.lat, snap.lng);
    if (dist < bestDist) {
      bestDist = dist;
      bestSnap = snap;
    }
  }
  return { snappedLat: bestSnap.lat, snappedLng: bestSnap.lng, distanceMeters: Math.round(bestDist) };
}

function snapToLocalRoads(lat, lng) {
  let bestSnap = null;
  let bestDist = Infinity;
  for (const road of LOCAL_ROADS) {
    const snap = snapToRoute(lat, lng, road.geometry);
    if (snap.distanceMeters < bestDist) {
      bestDist = snap.distanceMeters;
      bestSnap = {
        lat: snap.snappedLat,
        lng: snap.snappedLng,
        distance: snap.distanceMeters,
        name: road.name
      };
    }
  }
  return bestSnap;
}

const vehiclesDbRef = ref(db, "vehicles");
let count = 0;

console.log("Subscribing to Firebase vehicles node...");

const unsubscribe = onValue(vehiclesDbRef, (snapshot) => {
  const data = snapshot.val() || {};
  count++;
  console.log(`\n--- Update #${count} at ${new Date().toLocaleTimeString()} ---`);
  for (const [id, v] of Object.entries(data)) {
    if (id !== "TRUCK_1" && id !== "TRUCK_2") continue;
    const snap = snapToLocalRoads(v.lat, v.lng);
    console.log(`${id}: lat=${v.lat}, lng=${v.lng}, rawLat=${v.rawLat}, rawLng=${v.rawLng}`);
    console.log(`  Snap distance: ${snap?.distance}m, snappedLat=${snap?.lat?.toFixed(6)}, snappedLng=${snap?.lng?.toFixed(6)}`);
  }
  
  if (count >= 5) {
    console.log("\nUnsubscribing and exiting...");
    off(vehiclesDbRef);
    process.exit(0);
  }
});
