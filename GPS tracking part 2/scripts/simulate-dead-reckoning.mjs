import { loadEnv } from "./load_env.js";
loadEnv();

// scripts/simulate-dead-reckoning.mjs
// Run this script to simulate GPS loss and Dead Reckoning transition:
//   node scripts/simulate-dead-reckoning.mjs

import { initializeApp } from "firebase/app";
import { getDatabase, ref, set } from "firebase/database";

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

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function runSimulation() {
  console.log("🚀 Starting GPS Loss and Dead Reckoning simulation for TRUCK_1...");

  // Path starting near Jagannath Temple, Puri
  let lat = 19.8049;
  let lng = 85.8178;
  let bearing = 45.0;
  let speed = 25.0; // km/h

  // Phase 1: GPS Active (5 updates, 10s)
  console.log("\n📡 Phase 1: Active GPS lock (Green markers on dashboard)");
  for (let i = 1; i <= 5; i++) {
    // Move slightly northeast
    lat += 0.00015;
    lng += 0.00015;
    const timestamp = new Date().toISOString().replace("T", " ").slice(0, 19);

    const payload = {
      truck_id: "TRUCK_1",
      vehicle_id: "TRUCK_1",
      lat: Number(lat.toFixed(6)),
      lng: Number(lng.toFixed(6)),
      bearing: Number(bearing.toFixed(1)),
      is_moving: true,
      is_estimated: false,
      speed_kmh: Number(speed.toFixed(1)),
      speed: Math.round(speed),
      satellites: 8,
      hdop: 1.2,
      timestamp,
      online: true,
      rawLat: Number((lat + 0.00004).toFixed(6)), // mock slightly offset raw GPS
      rawLng: Number((lng - 0.00004).toFixed(6)),
      distanceMeters: 5,
    };

    await set(ref(db, "vehicles/TRUCK_1"), payload);
    console.log(`[GPS API] Step ${i}/5 - Lat: ${payload.lat}, Lng: ${payload.lng}, Speed: ${payload.speed_kmh} km/h`);
    await delay(5000);
  }

  // Phase 2: GPS Loss -> Dead Reckoning Mode (6 updates, 12s)
  console.log("\n⚠️ Phase 2: GPS Signal Lost! Switching to IMU-based Dead Reckoning (Orange markers / pulsing warning badges)");
  for (let i = 1; i <= 6; i++) {
    // Project along fused heading but decay speed by 10% each step (since ESP8266 decays speed on GPS loss)
    speed = speed * 0.90;
    if (speed < 1.0) speed = 0;

    // Estimate position change (speed in m/s * 5s update interval)
    const speedMps = (speed * 1000) / 3600;
    const distMoved = speedMps * 5.0;
    const headingRad = (bearing * Math.PI) / 180;

    const deltaLat = (distMoved * Math.cos(headingRad)) / 111132.95;
    const cosLat = Math.cos(lat * Math.PI / 180);
    const deltaLng = (distMoved * Math.sin(headingRad)) / (111132.95 * cosLat);

    lat += deltaLat;
    lng += deltaLng;

    // Gyroscope yaw rate simulation: steer slightly right (+5 degrees per step)
    bearing = (bearing + 5.0) % 360;

    const timestamp = new Date().toISOString().replace("T", " ").slice(0, 19);

    const payload = {
      truck_id: "TRUCK_1",
      vehicle_id: "TRUCK_1",
      lat: Number(lat.toFixed(6)),
      lng: Number(lng.toFixed(6)),
      bearing: Number(bearing.toFixed(1)),
      is_moving: speed > 0,
      is_estimated: true,
      speed_kmh: Number(speed.toFixed(1)),
      speed: Math.round(speed),
      satellites: 2, // Poor satellite count
      hdop: 6.5,     // Bad HDOP
      timestamp,
      online: true,
      rawLat: null,  // No GPS raw updates during total signal loss
      rawLng: null,
      distanceMeters: 0,
    };

    await set(ref(db, "vehicles/TRUCK_1"), payload);
    console.log(`[IMU DR] Step ${i}/6 - Lat: ${payload.lat}, Lng: ${payload.lng}, Speed: ${payload.speed_kmh} km/h (Estimated)`);
    await delay(5000);
  }

  // Phase 3: GPS Reacquired (3 updates, 6s)
  console.log("\n📡 Phase 3: GPS Signal Restored! Returning to normal lock (Green markers)");
  speed = 18.0;
  bearing = (bearing - 15.0) % 360;
  for (let i = 1; i <= 3; i++) {
    lat += 0.0001;
    lng += 0.0001;
    const timestamp = new Date().toISOString().replace("T", " ").slice(0, 19);

    const payload = {
      truck_id: "TRUCK_1",
      vehicle_id: "TRUCK_1",
      lat: Number(lat.toFixed(6)),
      lng: Number(lng.toFixed(6)),
      bearing: Number(bearing.toFixed(1)),
      is_moving: true,
      is_estimated: false,
      speed_kmh: Number(speed.toFixed(1)),
      speed: Math.round(speed),
      satellites: 9,
      hdop: 1.0,
      timestamp,
      online: true,
      rawLat: Number((lat + 0.00002).toFixed(6)),
      rawLng: Number((lng - 0.00002).toFixed(6)),
      distanceMeters: 2.5,
    };

    await set(ref(db, "vehicles/TRUCK_1"), payload);
    console.log(`[GPS API] Step ${i}/3 - Lat: ${payload.lat}, Lng: ${payload.lng}, Speed: ${payload.speed_kmh} km/h`);
    await delay(5000);
  }

  console.log("\n🎉 Simulation complete! Check the UI and verify markers, popup info, and details panel warning banners.");
}

runSimulation().catch(console.error);
