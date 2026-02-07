# GPS Recording System Documentation

## Overview
The GPS recording system automatically captures GPS data from Firebase and stores it in MongoDB whenever camera recording is active. This provides a complete trace of the vehicle's journey during each recording session.

---

## Data Flow

```
RFID Status = 1 (Start)
    ↓
Camera Recording Starts
    ↓
First Camera Connects
    ↓
GPS Recording Starts (Automatically)
    ↓
GPS data polled from Firebase every 3 seconds
    ↓
Each GPS reading stored in MongoDB
    ↓
RFID Status = 2 (Stop)
    ↓
Camera Recording Stops
    ↓
GPS Recording Stops (Automatically)
```

---

## MongoDB Structure

### Collection: `gps_recordings`

Each GPS record stored in MongoDB contains:

```javascript
{
  "device_name": "GJ1A0110",           // Vehicle device ID
  "date": "2025-12-11",                // Recording date
  "session_number": 1,                 // Session number (matches camera folder)
  "timestamp": "2025-12-11T10:15:30",  // When this record was saved
  
  "location": {
    "latitude": 23.0225,               // GPS latitude
    "longitude": 72.5714,              // GPS longitude
    "altitude": 150,                   // Altitude in meters
    "speed": 45,                       // Speed in km/h
    "satellites": 12,                  // Number of GPS satellites
    "gps_date": "11-12-2025",         // Date from GPS device
    "gps_time": "10:15:30",           // Time from GPS device
    "uid": "ACTIVE"                    // GPS UID status
  },
  
  "rfid": {
    "current": "C4 DE 71 06",         // Current RFID card
    "status": "1",                     // Status (1=Start, 2=Stop, etc.)
    "uid1": "C4 DE 71 06",            // RFID UID 1
    "uid2": "39 FD 70 06",            // RFID UID 2
    "uid3": "33 94 72 06",            // RFID UID 3
    "uid4": "72 1A EC 51"             // RFID UID 4
  },
  
  "device_info": {
    "imei": "932892404",               // Device IMEI
    "venue": "GJ1A0110",               // Vehicle venue/number
    "device_timestamp": "15215"        // Device timestamp
  }
}
```

---

## Folder/Session Organization

GPS data is organized to match the camera recording structure:

### Camera Recordings (File System):
```
RECORDINGS/
└── 2025-12-11/
    └── GJ1A0110/
        ├── 1/              ← Session 1
        ├── 2/              ← Session 2
        └── 3/              ← Session 3
```

### GPS Recordings (MongoDB):
```
gps_recordings collection:
├── Records with date="2025-12-11", device="GJ1A0110", session=1
├── Records with date="2025-12-11", device="GJ1A0110", session=2
└── Records with date="2025-12-11", device="GJ1A0110", session=3
```

**Query Example:**
```javascript
// Get all GPS records for session 1 of GJ1A0110 on 2025-12-11
db.gps_recordings.find({
  "device_name": "GJ1A0110",
  "date": "2025-12-11",
  "session_number": 1
}).sort({ "timestamp": 1 })
```

---

## Features

### ✅ Automatic Start/Stop
- **Starts**: When first camera connects successfully
- **Stops**: When camera recording is stopped
- **No manual intervention required**

### ✅ Continuous Polling
- GPS data fetched from Firebase every **3 seconds**
- Ensures high-resolution tracking
- Adjustable polling interval if needed

### ✅ Complete Data Capture
GPS records include:
- **Location**: Lat, Lon, Altitude, Speed, Satellites
- **RFID Info**: Current card, all UIDs, status
- **Device Info**: IMEI, venue, timestamps
- **Session Info**: Date, session number, MongoDB timestamp

### ✅ Synchronized with Camera Sessions
- Session numbers match camera folder numbers
- Same date organization
- Enables correlation between video and GPS data

### ✅ Error Handling
- Continues recording even if individual GPS polls fail
- Automatic cleanup on recording stop
- Thread-safe operations

---

## Technical Details

### Threading Model
- **GPS Recording Thread**: One per active recording session
- **Runs in background**: Daemon thread, doesn't block main process
- **Stop Signal**: Uses `threading.Event()` for clean shutdown

### Data Storage
- **Database**: Local MongoDB (`gps_server_db`)
- **Collection**: `gps_recordings`
- **Indexing**: Consider adding indexes for:
  - `{device_name: 1, date: 1, session_number: 1}`
  - `{timestamp: 1}`

### Logging
- Logs every 20 GPS records to reduce terminal clutter
- Shows total records when stopping
- Example output:
  ```
  📍 GPS RECORDING STARTED: GJ1A0110
     Session: 2025-12-11/GJ1A0110/1
     Polling interval: 3 seconds
  
  📍 GPS: GJ1A0110 - 20 records saved
  📍 GPS: GJ1A0110 - 40 records saved
  
  📍 GPS RECORDING STOPPED: GJ1A0110
     Total records saved: 47
  ```

---

## Usage Examples

### Querying GPS Data

**Get all GPS points for a session:**
```python
from mongodb import col_gps_recordings

records = col_gps_recordings.find({
    "device_name": "GJ1A0110",
    "date": "2025-12-11",
    "session_number": 1
}).sort("timestamp", 1)

for record in records:
    lat = record['location']['latitude']
    lon = record['location']['longitude']
    time = record['timestamp']
    print(f"{time}: ({lat}, {lon})")
```

**Get GPS route for visualization:**
```python
route_points = []
records = col_gps_recordings.find({
    "device_name": "GJ1A0110",
    "date": "2025-12-11",
    "session_number": 1
}, {
    "location.latitude": 1,
    "location.longitude": 1,
    "timestamp": 1
}).sort("timestamp", 1)

for record in records:
    route_points.append({
        "lat": record['location']['latitude'],
        "lng": record['location']['longitude'],
        "time": record['timestamp']
    })

# Use route_points to draw on map
```

**Calculate trip statistics:**
```python
from datetime import datetime

records = list(col_gps_recordings.find({
    "device_name": "GJ1A0110",
    "date": "2025-12-11",
    "session_number": 1
}).sort("timestamp", 1))

if records:
    # Duration
    start_time = datetime.fromisoformat(records[0]['timestamp'])
    end_time = datetime.fromisoformat(records[-1]['timestamp'])
    duration = end_time - start_time
    
    # Average speed
    speeds = [r['location']['speed'] for r in records if r['location']['speed'] > 0]
    avg_speed = sum(speeds) / len(speeds) if speeds else 0
    
    print(f"Duration: {duration}")
    print(f"Average Speed: {avg_speed:.2f} km/h")
    print(f"GPS Points: {len(records)}")
```

---

## Scaling Considerations

### Storage Estimation
- **Polling Rate**: 3 seconds = 20 records/minute = 1,200 records/hour
- **Record Size**: ~500 bytes per record
- **Storage per hour**: ~600 KB
- **Daily usage** (8 hours recording): ~4.8 MB per vehicle per day

### Performance
- MongoDB can handle millions of GPS records efficiently
- Consider adding indexes for frequently queried fields
- Implement data archival/cleanup for old sessions if needed

### Recommended Indexes
```javascript
// Composite index for session queries
db.gps_recordings.createIndex({
  "device_name": 1,
  "date": 1,
  "session_number": 1,
  "timestamp": 1
})

// Index for time-range queries
db.gps_recordings.createIndex({ "timestamp": 1 })
```

---

## Integration Points

### With Camera Recordings
GPS data can be synchronized with video recordings:
1. Video file: `RECORDINGS/2025-12-11/GJ1A0110/1/camera_name/recording.mp4`
2. GPS data: MongoDB query with same date, device, session
3. Timestamps allow precise correlation

### With RFID Events
RFID status changes are captured in GPS records:
- Each GPS record includes current RFID status
- Can identify exact GPS location when RFID events occurred
- Useful for tracking loading/unloading points

---

## Future Enhancements

Potential improvements:
1. **Real-time visualization**: Web dashboard showing live GPS tracking
2. **Geofencing**: Alerts when vehicle enters/exits predefined areas
3. **Route analysis**: Deviation detection, estimated arrival times
4. **Report generation**: Automatic trip reports with maps and statistics
5. **Data export**: Export GPS data to GPX, KML, or CSV formats

---

## Troubleshooting

### GPS not recording
1. Check if camera recording is active
2. Verify Firebase connectivity
3. Check MongoDB connection
4. Look for errors in console output

### Missing GPS data
- Ensure device is sending GPS data to Firebase
- Check `location` object in Firebase has valid lat/lon
- Verify polling interval isn't too long

### MongoDB storage issues
- Check disk space
- Verify MongoDB is running
- Check collection permissions

---

## Files Modified

1. **mongodb.py**: Added `col_gps_recordings` collection
2. **app.py**: Added GPS recording functions and integration
   - `record_gps_data()`: Background thread for GPS polling
   - `start_gps_recording()`: Starts GPS recording
   - `stop_gps_recording()`: Stops GPS recording
   - Integration in `auto_start_recording()` and `auto_stop_recording()`
