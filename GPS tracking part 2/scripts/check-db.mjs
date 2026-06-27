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

async function check() {
  const vSnap = await get(ref(db, "vehicles"));
  console.log("=== VEHICLES ===");
  console.log(JSON.stringify(vSnap.val(), null, 2));

  const cSnap = await get(ref(db, "commands"));
  console.log("\n=== COMMANDS ===");
  console.log(JSON.stringify(cSnap.val(), null, 2));
}

check().then(() => process.exit(0)).catch(console.error);
