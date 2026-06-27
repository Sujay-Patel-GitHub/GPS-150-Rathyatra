import { loadEnv } from "./load_env.js";
loadEnv();

// scripts/simulate-multi-truck.mjs
// Run this script to simulate both TRUCK_1 and TRUCK_2 moving and handle real-time calibration:
//   node scripts/simulate-multi-truck.mjs

import { initializeApp } from "firebase/app";
import { getDatabase, ref, set, onValue } from "firebase/database";

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

// YATRA_ROUTE from constants.js (Jamalpur to Saraspur to Jamalpur Loop)
const YATRA_ROUTE = [
  [23.0114, 72.5809], // Jagannath Temple, Jamalpur (Start)
  [23.0135, 72.5818], // Jamalpur Gate
  [23.0150, 72.5830], // Jamalpur Crossroads
  [23.0178, 72.5862], // Khamasa (AMC Office)
  [23.0181, 72.5912], // Astodia Chakla
  [23.0205, 72.5938], // Raipur Darwaja
  [23.0263, 72.5962], // Khadia Crossroads
  [23.0270, 72.5975], // Sarangpur Gate
  [23.0285, 72.5985], // Panchkuva Darwaja
  [23.0305, 72.6002], // Kalupur Circle / Railway Station
  [23.0350, 72.6050], // Saraspur Bridge
  [23.0375, 72.6105], // Ranchhodraji Temple, Saraspur (Halt Point)
  [23.0350, 72.6050], // Saraspur Bridge (Return)
  [23.0305, 72.6002], // Kalupur Circle (Return)
  [23.0382, 72.5945], // Prem Darwaja
  [23.0395, 72.5895], // Dariapur Darwaja
  [23.0378, 72.5842], // Delhi Chakla / Delhi Darwaja
  [23.0425, 72.5762], // Shahpur Darwaja
  [23.0370, 72.5780], // Halim ni Khadki
  [23.0315, 72.5835], // Gheekanta
  [23.0255, 72.5845], // Pankore Naka
  [23.0232, 72.5862], // Manek Chowk
  [23.0178, 72.5862], // Khamasa
  [23.0150, 72.5830], // Jamalpur Crossroads
  [23.0114, 72.5809]  // Jagannath Temple, Jamalpur (End)
];

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Calculate bearing between two coordinate points
function calculateBearing(lat1, lng1, lat2, lng2) {
  const dLng = (lng2 - lng1) * Math.PI / 180;
  const lat1Rad = lat1 * Math.PI / 180;
  const lat2Rad = lat2 * Math.PI / 180;
  const y = Math.sin(dLng) * Math.cos(lat2Rad);
  const x = Math.cos(lat1Rad) * Math.sin(lat2Rad) - Math.sin(lat1Rad) * Math.cos(lat2Rad) * Math.cos(dLng);
  let brng = Math.atan2(y, x) * 180 / Math.PI;
  return (brng + 360) % 360;
}

// Track calibration state
const calibrationState = {
  TRUCK_1: { pending: false, offset: 0 },
  TRUCK_2: { pending: false, offset: 0 }
};

// Set up closed-loop listeners for calibration requests from the website dashboard
function setupCalibrationListeners() {
  ["TRUCK_1", "TRUCK_2"].forEach((truckId) => {
    const calRef = ref(db, `vehicles/${truckId}/calibrate_pending`);
    onValue(calRef, async (snapshot) => {
      const isPending = snapshot.val();
      if (isPending !== null && isPending !== false) {
        console.log(`⚙️  [IMU HW Simulator] RECEIVED CALIBRATION COMMAND for ${truckId}!`);
        console.log(`   [IMU HW Simulator] Performing 200-sample stationary Gyro Z offset alignment...`);
        
        await delay(1200); // Simulate hardware time to compute offset
        
        calibrationState[truckId].offset = 0; // Gyro offset cleared
        console.log(`   [IMU HW Simulator] ✅ ${truckId} calibration SUCCESS. Zero-bias offset aligned.`);
        
        // Write success states back to Firebase
        await set(ref(db, `vehicles/${truckId}/calibrate_pending`), false);
        await set(ref(db, `vehicles/${truckId}/is_calibrated`), true);
        await set(ref(db, `vehicles/${truckId}/last_calibrated_at`), new Date().toISOString());
      }
    });
  });
}

async function runSimulation() {
  console.log("🚀 Starting Multi-Truck GPS and IMU calibration simulation...");
  setupCalibrationListeners();

  let t1Index = 0;
  let t2Index = 4; // Start Truck 2 slightly ahead

  // Run for 30 cycles (60 seconds of movement)
  for (let cycle = 1; cycle <= 30; cycle++) {
    const timestamp = new Date().toISOString().replace("T", " ").slice(0, 19);

    // TRUCK 1 logic
    const t1Curr = YATRA_ROUTE[t1Index];
    const t1Next = YATRA_ROUTE[(t1Index + 1) % YATRA_ROUTE.length];
    const t1Bearing = calculateBearing(t1Curr[0], t1Curr[1], t1Next[0], t1Next[1]);
    
    // Add minor raw GPS drift to show snapper at work
    const t1RawLat = t1Curr[0] + (Math.random() - 0.5) * 0.00015;
    const t1RawLng = t1Curr[1] + (Math.random() - 0.5) * 0.00015;

    const t1Payload = {
      truck_id: "TRUCK_1",
      vehicle_id: "TRUCK_1",
      lat: t1Curr[0],
      lng: t1Curr[1],
      rawLat: Number(t1RawLat.toFixed(6)),
      rawLng: Number(t1RawLng.toFixed(6)),
      bearing: Number(t1Bearing.toFixed(1)),
      is_moving: true,
      is_estimated: false,
      speed_kmh: 12.5,
      speed: 13,
      satellites: 8,
      hdop: 1.1,
      timestamp,
      online: true,
      distanceMeters: Math.round(Math.random() * 8),
    };

    // TRUCK 2 logic
    const t2Curr = YATRA_ROUTE[t2Index];
    const t2Next = YATRA_ROUTE[(t2Index + 1) % YATRA_ROUTE.length];
    const t2Bearing = calculateBearing(t2Curr[0], t2Curr[1], t2Next[0], t2Next[1]);

    const t2RawLat = t2Curr[0] + (Math.random() - 0.5) * 0.00015;
    const t2RawLng = t2Curr[1] + (Math.random() - 0.5) * 0.00015;

    // Simulate occasional weak signal for Truck 2 to trigger visual warning badges
    const isWeakSignal = cycle >= 10 && cycle <= 16;

    const t2Payload = {
      truck_id: "TRUCK_2",
      vehicle_id: "TRUCK_2",
      lat: t2Curr[0],
      lng: t2Curr[1],
      rawLat: Number(t2RawLat.toFixed(6)),
      rawLng: Number(t2RawLng.toFixed(6)),
      bearing: Number(t2Bearing.toFixed(1)),
      is_moving: true,
      is_estimated: isWeakSignal, // estimated when signal drops
      speed_kmh: isWeakSignal ? 8.2 : 15.0,
      speed: isWeakSignal ? 8 : 15,
      satellites: isWeakSignal ? 3 : 10,
      hdop: isWeakSignal ? 5.2 : 0.9,
      timestamp,
      online: true,
      distanceMeters: isWeakSignal ? 0 : Math.round(Math.random() * 5),
    };

    // Send updates
    await set(ref(db, "vehicles/TRUCK_1"), t1Payload);
    await set(ref(db, "vehicles/TRUCK_2"), t2Payload);

    console.log(`[Cycle ${cycle}/30] - TRUCK_1 index: ${t1Index} | TRUCK_2 index: ${t2Index} (Estimated: ${t2Payload.is_estimated})`);

    // Progress route indices
    t1Index = (t1Index + 1) % YATRA_ROUTE.length;
    t2Index = (t2Index + 1) % YATRA_ROUTE.length;

    await delay(5000);
  }

  console.log("\n🎉 Multi-truck movement simulation completed.");
  process.exit(0);
}

runSimulation().catch(console.error);
