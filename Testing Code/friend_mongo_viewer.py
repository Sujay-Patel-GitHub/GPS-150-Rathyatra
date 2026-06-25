"""
Run this on your system to view live GPS data from the server.
Requirements: pip install pymongo
"""

from pymongo import MongoClient
from datetime import datetime
import time

SERVER_IP = "150.129.165.162"
PORT      = 27017

client = MongoClient(SERVER_IP, PORT, serverSelectionTimeoutMS=5000)

try:
    client.server_info()
    print(f"Connected to MongoDB at {SERVER_IP}:{PORT}\n")
except Exception as e:
    print(f"Cannot connect: {e}")
    exit(1)

db         = client["gps_server_db"]
col_test   = db["test"]

print("=" * 55)
print("  LIVE GPS FEED  —  refreshes every 3 seconds")
print("  Press Ctrl+C to stop")
print("=" * 55)

last_id = None

while True:
    try:
        # Fetch latest 10 entries
        entries = list(col_test.find().sort("timestamp", -1).limit(10))

        if not entries:
            print("No data yet — waiting for ESP to push GPS...", end="\r")
        else:
            # Only print if there is a new entry
            newest = entries[0]
            if newest["_id"] != last_id:
                last_id = newest["_id"]
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] New GPS point received!")
                print(f"  Lat : {newest['lat']}")
                print(f"  Lng : {newest['lng']}")
                print(f"  Time: {newest['timestamp'].strftime('%d-%b-%Y %I:%M:%S %p')}")
                print("-" * 40)
                print(f"  Total points stored: {col_test.count_documents({})}")

        time.sleep(3)

    except KeyboardInterrupt:
        print("\nStopped.")
        break
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(3)
