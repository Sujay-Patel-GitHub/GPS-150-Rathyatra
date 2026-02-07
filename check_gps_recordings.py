"""
Quick script to check GPS recordings in MongoDB
"""
from mongodb import col_gps_recordings
from datetime import datetime

print("\n" + "="*60)
print("GPS RECORDINGS CHECK")
print("="*60 + "\n")

# Get total count
total_count = col_gps_recordings.count_documents({})
print(f"📊 Total GPS records in database: {total_count}\n")

if total_count > 0:
    # Get latest 5 records
    print("📍 Latest 5 GPS records:")
    print("-" * 60)
    
    latest_records = col_gps_recordings.find().sort("timestamp", -1).limit(5)
    
    for i, record in enumerate(latest_records, 1):
        device = record.get('device_name', 'N/A')
        date = record.get('date', 'N/A')
        session = record.get('session_number', 'N/A')
        timestamp = record.get('timestamp', 'N/A')
        
        location = record.get('location', {})
        lat = location.get('latitude', 0)
        lon = location.get('longitude', 0)
        speed = location.get('speed', 0)
        
        print(f"\n{i}. {device} - Session {session}")
        print(f"   Date: {date}")
        print(f"   Time: {timestamp}")
        print(f"   Location: ({lat}, {lon})")
        print(f"   Speed: {speed} km/h")
    
    print("\n" + "-" * 60)
    
    # Group by session
    print("\n📁 Records by session:")
    print("-" * 60)
    
    pipeline = [
        {
            "$group": {
                "_id": {
                    "device": "$device_name",
                    "date": "$date",
                    "session": "$session_number"
                },
                "count": {"$sum": 1},
                "first_time": {"$min": "$timestamp"},
                "last_time": {"$max": "$timestamp"}
            }
        },
        {"$sort": {"_id.date": -1, "_id.session": -1}}
    ]
    
    sessions = list(col_gps_recordings.aggregate(pipeline))
    
    for session in sessions:
        device = session['_id']['device']
        date = session['_id']['date']
        session_num = session['_id']['session']
        count = session['count']
        first = session['first_time']
        last = session['last_time']
        
        # Calculate duration
        try:
            start_dt = datetime.fromisoformat(first)
            end_dt = datetime.fromisoformat(last)
            duration = end_dt - start_dt
            duration_str = str(duration).split('.')[0]
        except:
            duration_str = "N/A"
        
        print(f"\n{device} / {date} / Session {session_num}")
        print(f"   GPS Records: {count}")
        print(f"   Duration: {duration_str}")
        print(f"   Period: {first[:19]} to {last[:19]}")
    
else:
    print("⚠️  No GPS records found yet.")
    print("   GPS recording starts when camera recording begins.")

print("\n" + "="*60 + "\n")
