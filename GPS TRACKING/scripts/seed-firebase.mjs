import { loadEnv } from "./load_env.js";
loadEnv();

// scripts/seed-firebase.mjs
// Run once to populate test vehicle data:
//   node scripts/seed-firebase.mjs
//
// Requires:  VITE_FIREBASE_* in .env.local  OR  set env vars manually below.

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

// Puri, Odisha — Rath Yatra route area
const vehicles = {
  TRUCK_01: {
    vehicle_id: "TRUCK_01",
    lat: 19.8050,
    lng: 85.8312,
    speed: 12,
    timestamp: new Date().toISOString().replace("T", " ").slice(0, 19),
  },
  TRUCK_02: {
    vehicle_id: "TRUCK_02",
    lat: 19.8135,
    lng: 85.8180,
    speed: 8,
    timestamp: new Date().toISOString().replace("T", " ").slice(0, 19),
  },
};

for (const [id, data] of Object.entries(vehicles)) {
  await set(ref(db, `vehicles/${id}`), data);
  console.log(`✅ Seeded ${id}:`, data);
}

console.log("\n🎉 Done! Open the dashboard — both vehicles should appear on the map.");
process.exit(0);
