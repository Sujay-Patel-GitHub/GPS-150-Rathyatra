# 📊 MongoDB Database Selection (gps_server_db)

This document outlines the structure of the MongoDB database used in the GPS Tracking & Camera Recording system. You can use this "sheet" to explain the database setup to others or recreate it for a new program.

---

## 🏗️ 1. Core Structure
**Database Name:** `gps_server_db`

The system uses **8 main collections** to manage users, vehicles, settings, and historical GPS data.

### 👥 User Collections (4 Total)
These collections store credentials and personal info for different roles. All follow a similar structure.

| Collection Name | Description | Fields |
| :--- | :--- | :--- |
| `godown_managers` | Warehouse/Godown supervisors | `name`, `username`, `password`, `mobile`, `email`, `role` |
| `transporters` | Delivery/Shipping partners | `name`, `username`, `password`, `mobile`, `email`, `role` |
| `drivers` | Vehicle operators | `name`, `username`, `password`, `mobile`, `email`, `role` |
| `shop_keepers` | FPS / Outlet managers | `name`, `username`, `password`, `mobile`, `email`, `role` |

---

### 🚛 2. Registered Vehicles (`registered_vehicles`)
This is the heart of the system, linking hardware IDs to real-world data.

*   **Primary Key:** `device_id` (The hardware UID from the device)
*   **Fields:**
    *   `device_id`: (string) e.g., "HWD-12345"
    *   `rc_number`: (string) Vehicle registration number.
    *   `driver_name`: (string) Name of the assigned driver.
    *   `transporter_name`: (string) Name of the assigned transporter.
    *   `godown_manager`: (string) Name of the assigned manager.
    *   `rtmp_source`: (string) Preference for video stream ("firebase" or "direct").
    *   `time_offsets`: (object) Used to fix incorrect device clocks.
        *   `y`: Year offset
        *   `m`: Month offset
        *   `d`: Day offset
        *   `h`: Hour offset
        *   `min`: Minute offset

---

### ⚙️ 3. Application Settings (`settings`)
Stores global configuration values.

*   **Document ID:** `add_user_format`
    *   `format`: JSON object containing labels for the user creation UI.
*   **Document ID:** `rtmp_source_pref`
    *   `source`: "firebase" or "direct"
*   **Document ID:** `power_off_config`
    *   `minutes`: Threshold for "POWER OFF" status (default: 60).

---

### 📍 4. GPS & Map Data
These collections handle the high-frequency data for tracking.

| Collection Name | Description | Key Fields |
| :--- | :--- | :--- |
| `gps_recordings` | Historical GPS logs stored during camera recording sessions. | `date`, `device_id`, `session_num`, `history` (array of lat/lng/time) |
| `map_recordings` | High-accuracy data for the "Live Map Recording" feature. | `device_id`, `timestamp`, `lat`, `lng`, `speed`, `sat` |

**Optimized Index:**
The `map_recordings` collection uses a compound index for speed:
`db.map_recordings.createIndex({ "device_id": 1, "timestamp": 1 })`

---

## 🐍 Python Connection Example (Reference)
If you are moving to a new program, use this code to connect to these collections:

```python
from pymongo import MongoClient

# Connect to Local or Remote Server
client = MongoClient("mongodb://localhost:27017/")
db = client["gps_server_db"]

# Access Collections
vehicles = db["registered_vehicles"]
settings = db["settings"]
gps_data = db["gps_recordings"]

# Example: Find a vehicle
vehicle_info = vehicles.find_one({"device_id": "YOUR_DEVICE_ID"})
print(vehicle_info['rc_number'])
```

---

## 📅 Maintenance Info
*   **Storage Type:** NoSQL (JSON-like documents)
*   **Default Port:** 27017
*   **Primary Filter:** Most lookups use `device_id` or `username`.
