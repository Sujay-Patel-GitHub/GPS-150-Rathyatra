import { loadEnv } from "./load_env.js";
loadEnv();

import { initializeApp } from "firebase/app";
import { getDatabase, ref, get } from "firebase/database";

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

async function run() {
  const snapshot = await get(ref(db, "recordings"));
  if (snapshot.exists()) {
    console.log("Recordings found:");
    const recordings = snapshot.val();
    for (const [vehicleId, sessions] of Object.entries(recordings)) {
      console.log(`\nVehicle: ${vehicleId}`);
      for (const [sessionId, session] of Object.entries(sessions)) {
        console.log(`  Session ID: ${sessionId}`);
        console.log(`    Points count: ${session.pointCount}`);
        console.log(`    Start Time: ${session.startTime}`);
        console.log(`    End Time: ${session.endTime}`);
        if (session.points && session.points.length > 0) {
          console.log(`    First 3 points:`, session.points.slice(0, 3));
          console.log(`    Last 3 points:`, session.points.slice(-3));
        }
      }
    }
  } else {
    console.log("No recordings found in Firebase.");
  }
  process.exit(0);
}

run().catch(console.error);
