# 🛕 Rath Yatra GPS Fleet Tracker

Real-time GPS fleet tracking dashboard for Rath Yatra procession vehicles.  
Built with **React 19 + Vite**, **Firebase Realtime Database**, **Google Maps JavaScript API**, and **Tailwind CSS v4**.

---

## Features

- 🗺️ **Live Google Map** with dark theme and custom SVG vehicle markers
- 🟢 **Real-time status** — Online/Offline badges that update every 5 seconds
- 🔥 **Firebase Realtime Database** subscriptions — zero-latency updates
- 📍 **Marker click / sidebar click** reveals a detail panel with all GPS data
- 🧭 **Navigate button** opens Google Maps directions to the vehicle
- 📱 **Full-screen responsive** layout

---

## Project Structure

```
src/
├── components/
│   ├── DetailPanel/      # Selected vehicle stats + mini-map
│   ├── LoadingScreen/    # Initial connection splash
│   ├── MapView/          # Google Maps + markers + info windows
│   └── Sidebar/          # Vehicle list with status badges
├── hooks/
│   └── useVehicles.js    # Firebase real-time subscription
├── lib/
│   ├── constants.js      # Vehicle IDs, thresholds, map defaults
│   └── firebase.js       # Firebase app initialization
└── utils/
    └── formatters.js     # Time, coord, speed, URL helpers
```

---

## Quick Start

### 1. Clone / open the project

```bash
cd rath-yatra-tracker
```

### 2. Configure environment variables

```bash
cp .env.example .env.local
```

Edit `.env.local` and fill in:

| Variable | Description |
|---|---|
| `VITE_FIREBASE_API_KEY` | Firebase API key |
| `VITE_FIREBASE_AUTH_DOMAIN` | Firebase auth domain |
| `VITE_FIREBASE_DATABASE_URL` | Realtime Database URL (includes region) |
| `VITE_FIREBASE_PROJECT_ID` | Firebase project ID |
| `VITE_FIREBASE_APP_ID` | Firebase app ID |
| `VITE_GOOGLE_MAPS_API_KEY` | Google Maps API key (Maps JS API enabled) |

### 3. Install & run

```bash
npm install
npm run dev
```

---

## Firebase Data Format

Write vehicle data under `vehicles/TRUCK_01` and `vehicles/TRUCK_02`:

```json
{
  "vehicle_id": "TRUCK_01",
  "lat": 19.8135,
  "lng": 85.8312,
  "speed": 15,
  "timestamp": "2026-06-09 10:30:00"
}
```

### Add more vehicles

1. Open `src/lib/constants.js`
2. Append to `VEHICLE_IDS`: `["TRUCK_01", "TRUCK_02", "TRUCK_03", ...]`
3. Add labels/icons in `Sidebar.jsx`'s `vehicleLabels` / `vehicleIcons` maps

That's it — no other code changes needed.

---

## Firebase Security Rules (for production)

```json
{
  "rules": {
    "vehicles": {
      ".read": true,
      ".write": false
    }
  }
}
```

> ESP32 devices write via the REST API or Firebase Admin SDK on a backend.

---

## ESP32 Integration

The ESP32 + NEO-6M GPS module should HTTP POST (or use Firebase REST) to:

```
PUT https://<project>.firebaseio.com/vehicles/TRUCK_01.json
Authorization: Bearer <your-database-secret>
Content-Type: application/json

{
  "vehicle_id": "TRUCK_01",
  "lat": 19.8135,
  "lng": 85.8312,
  "speed": 12,
  "timestamp": "2026-06-09 10:30:00"
}
```

---

## License

MIT
