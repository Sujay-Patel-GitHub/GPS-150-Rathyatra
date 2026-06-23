# Video Recording Camera Source Fix

## Problem
When selecting MongoDB database from RTMP management, the video recording webpage was still fetching camera links from Firebase instead of MongoDB.

## Root Cause
The `/get_vehicle_cameras/<vehicle_id>` endpoint was missing from `app.py`. This endpoint is called by `recordings.html` (line 807) to fetch the list of cameras for a selected vehicle.

## Solution
Added the `/get_vehicle_cameras/<vehicle_id>` endpoint to `app.py` that:

1. **Checks RTMP Source Preference**: Reads the `rtmp_source` field from the vehicle document in MongoDB
2. **Fetches from Correct Source**:
   - If `rtmp_source == 'mongo'`: Fetches camera links from `vehicle.mongo_rtmp` in MongoDB
   - If `rtmp_source == 'firebase'`: Fetches camera links from Firebase real-time database
3. **Returns Camera List**: Returns a JSON response with camera IDs and names extracted from RTMP URLs

## Code Changes

### File: `app.py`
**Location**: After line 3950 (before `toggle_camera` route)

**Added Endpoint**:
```python
@app.route("/get_vehicle_cameras/<vehicle_id>")
def get_vehicle_cameras(vehicle_id):
    """Get camera information for a vehicle based on RTMP source preference"""
    # Gets vehicle from MongoDB
    # Checks rtmp_source preference
    # Returns cameras from MongoDB or Firebase accordingly
```

## How It Works

1. **User selects vehicle** in video recordings page
2. **JavaScript calls** `/get_vehicle_cameras/<vehicle_id>`
3. **Backend checks** `vehicle.rtmp_source` in MongoDB
4. **Backend fetches cameras** from:
   - `vehicle.mongo_rtmp.rtmp1-4` if source is 'mongo'
   - Firebase `data/<vehicle_id>/rtmp1-4` if source is 'firebase'
5. **Frontend receives** camera list and displays only configured cameras

## Testing
To verify the fix:
1. Go to RTMP Management
2. Select a vehicle and set source to "MongoDB"
3. Configure MongoDB camera links
4. Go to Video Recordings page
5. Select the same vehicle
6. Camera dropdown should show MongoDB camera names (extracted from RTMP URLs)

## Related Files
- `app.py` - Added endpoint
- `recordings.html` - Calls the endpoint (line 807)
- `templates/manage_rtmp.html` - Where RTMP source is configured
