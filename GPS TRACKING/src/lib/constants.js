// src/lib/constants.js
// Central configuration for the Rath Yatra GPS Tracking System
// 150 trucks | Ahmedabad route

// ── Vehicle Registry ────────────────────────────────────────────────────────
// Active tracked vehicles
export const ACTIVE_VEHICLE_IDS = ["TRUCK_1", "TRUCK_2", "TRUCK_10"];
export const VEHICLE_IDS = ["TRUCK_1", "TRUCK_2", "TRUCK_10"];

export const vehicleLabels = {
  TRUCK_1: "Truck 1",
  TRUCK_01: "Chariot 01",
  TRUCK_02: "Chariot 02",
  TRUCK_10: "Truck 10",
};

export const vehicleIcons = {
  TRUCK_1: "🚛",
  TRUCK_01: "🚌",
  TRUCK_02: "🚌",
  TRUCK_10: "🚛",
};

// Mapping of Physical Device Database Keys to Dashboard Display IDs
// (e.g. physical device "TRUCK_2  " displays as "TRUCK_2" on the dashboard)
// Maps keys with trailing spaces from the firmware to clean dashboard IDs.
export const DEVICE_TO_DISPLAY_MAP = {
  "TRUCK_1  ": "TRUCK_1",
  "TRUCK_2  ": "TRUCK_2",
  "TRUCK_10": "TRUCK_10",
  "TRUCK_10  ": "TRUCK_10"
};

// Mapping of Dashboard Display IDs to Physical Device Database Keys
// (used to route commands like calibration back to the correct physical database path)
export const DISPLAY_TO_DEVICE_MAP = {
  "TRUCK_1": "TRUCK_1  ",
  "TRUCK_2": "TRUCK_2  ",
  "TRUCK_10": "TRUCK_10"
};

// ── Geofence SMS Phone Numbers ──────────────────────────────────────────────
export const TRUCK_PHONES = {
  "TRUCK_1": "9825902865",
  "TRUCK_2": "8200741406",
  "TRUCK_10": "8469091377"
};

// How many seconds without update before marking offline
export const OFFLINE_THRESHOLD_SECONDS = 90;

// ── Map defaults — Walled City (Ahmedabad old city) area ──────────────────────
export const DEFAULT_MAP_CENTER = { lat: 23.0265, lng: 72.5465 };
export const DEFAULT_MAP_ZOOM = 18;

// ── Rath Yatra Official Route (Jamalpur → Saraspur → Jamalpur Loop) ──────────
export const YATRA_ROUTE = [
  [23.026546076995007, 72.54619270563127],
  [23.026649753979722, 72.54627853631975],
  [23.02673615140607, 72.54635632038118],
  [23.02683982824458, 72.54644483327867],
  [23.02691635157425, 72.54651188850404],
  [23.026980532397907, 72.5465950369835],
  [23.027012622798264, 72.54666477441789],
  [23.027017559782273, 72.54674524068834],
  [23.026995343352855, 72.54681497812273],
  [23.026955847469342, 72.54689812660219],
  [23.02692128856177, 72.54694104194643],
  [23.026881792656553, 72.54689812660219],
  [23.026802800811414, 72.54688471555711],
  [23.026713934930292, 72.5468820333481],
  [23.02661025799494, 72.546863257885],
  [23.026546076995007, 72.54688739776613],
  [23.026472021957122, 72.54690617322923],
  [23.026395498375223, 72.54690617322923],
  [23.026348596803558, 72.54692494869234],
  [23.026301695215576, 72.54693835973741],
  [23.026242451081117, 72.54694104194643],
  [23.02618814393498, 72.54694908857347],
  [23.02614124229118, 72.5469544529915],
  [23.026086935104285, 72.54693835973741],
  [23.026040033425257, 72.54690885543825]
];

// ── Alert thresholds ────────────────────────────────────────────────────────
export const ALERT = {
  TOO_CLOSE_METERS: 25,   // < 25m → too close
  GAP_LARGE_METERS: 200,  // > 200m → gap too large
  OFF_ROUTE_METERS: 50,  // > 50m from route → off-route
  HDOP_WARN: 3.0,  // HDOP > 3 → weak signal
  HDOP_BAD: 5.0,  // HDOP > 5 → unreliable
  MIN_SATELLITES: 4,    // < 4 sats → poor signal
  MAX_SPEED_KMH: 20,   // > 20 km/h → suspicious
  SIGNAL_LOST_SEC: 45,   // No update for 45s → lost
};

// ── Marker colors ────────────────────────────────────────────────────────────
export const STATUS_COLOR = {
  online: "#22c55e",  // green  — normal, good GPS
  weak: "#f59e0b",  // amber  — HDOP > 3 or estimated
  jammed: "#ef4444",  // red    — GPS jammed / too close / off-route
  lost: "#6b7280",  // gray   — no signal > 60s
  offline: "#374151",  // dark   — never connected
};

// Trail colors per vehicle index (cycles)
export const TRAIL_COLORS = [
  "#4285F4", "#EA4335", "#FBBC04", "#34A853",
  "#FF6D00", "#9C27B0", "#00BCD4", "#FF5722",
];

// ── Geofenced Landmarks ──────────────────────────────────────────────────────
export const LANDMARKS = [];
