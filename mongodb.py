from pymongo import MongoClient
import sys

# --- MONGODB CONFIGURATION ---
try:
    # Connect to local MongoDB
    mongo_client = MongoClient("mongodb://localhost:27017/", w=1, journal=True)

    # Main Database: gps_server_db
    db = mongo_client["gps_server_db"]

    # --- FOLDER/COLLECTION STRUCTURE ---
    # 1. Godown Managers Folder
    col_godown = db["godown_managers"]

    # 2. Transporters Folder
    col_transporters = db["transporters"]

    # 3. Drivers Folder
    col_drivers = db["drivers"]

    # 4. Shop Keepers (FPS) Folder
    col_shopkeepers = db["shop_keepers"]

    # 5. Registered Vehicles Folder (Assignments)
    # Stores: { "device_id": "...", "driver_name": "...", "transporter_name": "...", "rc_number": "..." }
    if "registered_trucks" in db.list_collection_names():
        col_vehicles = db["registered_trucks"]
    else:
        col_vehicles = db["registered_vehicles"]

    # 6. Settings (General App Config)
    col_settings = db["settings"]
    
    # 7. GPS Recordings Collection
    # Stores GPS data recorded during camera recording sessions
    # Structure: date > vehicle_number > session_number > [GPS records]
    col_gps_recordings = db["gps_recordings"]
    
    # 8. Live Map Recordings (from "View Live")
    col_map_recordings = db["map_recordings"]

    # 9. SOS Logs (emergency signals raised from devices)
    col_sos_logs = db["sos_logs"]

    # 10. Live GPS — latest GPS point per device, pushed directly by devices
    col_gps_live = db["gps_live"]

    # 11. Test collection — lat/lng from ESP test device
    col_test = db["test"]

    # 12. Route collection
    col_route = db["route"]

    # 13. Area collection
    col_area = db["area"]

    # 14. SMS Sender collection
    col_sms_sender = db["sms_sender"]

    # 15. SMS Queue collection
    col_sms_queue = db["sms_queue"]

    # Ensure collections exist in the database
    if "route" not in db.list_collection_names():
        db.create_collection("route")
    if "area" not in db.list_collection_names():
        db.create_collection("area")
    if "sms_sender" not in db.list_collection_names():
        db.create_collection("sms_sender")
    if "sms_queue" not in db.list_collection_names():
        db.create_collection("sms_queue")

    # Create indexes for optimization
    col_map_recordings.create_index([("device_id", 1), ("timestamp", 1)])
    col_sos_logs.create_index([("started_at", -1)])
    col_gps_live.create_index([("device_id", 1)], unique=True)

    print("✅ Connected to Local MongoDB")
    print("✅ Database Structure & Indexes Initialized.")

except ImportError:
    print("❌ CRITICAL ERROR: 'pymongo' not installed. Run: pip install pymongo")
    sys.exit(1)
except Exception as e:
    print(f"❌ CRITICAL ERROR: Could not connect to MongoDB: {e}")
    sys.exit(1)