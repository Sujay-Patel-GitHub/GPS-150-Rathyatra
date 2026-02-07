from pymongo import MongoClient
import sys

# --- MONGODB CONFIGURATION ---
try:
    # Connect to local MongoDB
    mongo_client = MongoClient("mongodb://localhost:27017/")

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
    col_vehicles = db["registered_vehicles"]

    # 6. Settings (General App Config)
    col_settings = db["settings"]
    
    # 7. GPS Recordings Collection
    # Stores GPS data recorded during camera recording sessions
    # Structure: date > vehicle_number > session_number > [GPS records]
    col_gps_recordings = db["gps_recordings"]
    
    # 8. Live Map Recordings (from "View Live")
    col_map_recordings = db["map_recordings"]

    print("✅ Connected to Local MongoDB")
    print("✅ Database Structure: Separate Collections for Roles & Vehicles Initialized.")

except ImportError:
    print("❌ CRITICAL ERROR: 'pymongo' not installed. Run: pip install pymongo")
    sys.exit(1)
except Exception as e:
    print(f"❌ CRITICAL ERROR: Could not connect to MongoDB: {e}")
    sys.exit(1)