# Recording Folder Creation Feature

## Overview
This feature automatically creates a structured folder hierarchy when you click the "Start Recording" button on the Recordings page. The folder structure is organized by date, vehicle, RFID status, and camera.

## Folder Structure
```
RECORDINGS/
└── 2025-12-09/              (Current date)
    └── VehicleNumber/        (Device/Vehicle name)
        └── 1/                (RFID Status number)
            ├── camera1/      (Camera name from RTMP URL)
            ├── camera2/
            ├── camera3/
            └── camera4/
```

## RFID Status Mapping
- **1** = Start (RFID Status: Start)
- **2** = Stop (RFID Status: Stop)
- **3** = Load (RFID Status: Load)
- **4** = Unload (RFID Status: Unload)
- **0** = N/A (No RFID status)

## How It Works

### 1. Frontend (recordings.html)
- Added a "Start Recording" button to each vehicle card
- Button is only enabled for registered vehicles
- Button shows loading state while creating folders
- Success/error messages are displayed via alerts
- Button changes color to green when successful

### 2. Backend (app.py)
- New route: `/create_recording_folder` (POST)
- Fetches current date from system
- Gets RFID status from the request
- Fetches camera RTMP URLs from Firebase
- Extracts camera names from RTMP URLs (last segment after `/`)
- Creates the complete folder structure
- Returns success status and folder path

### 3. Camera Name Extraction
- Camera names are extracted from RTMP URLs stored in Firebase
- For example: `rtmp://server/stream/camera_front` → folder name: `camera_front`
- If RTMP URL is `rtmp://192.168.1.100/live/cam1` → folder name: `cam1`
- If no cameras are found, defaults to: camera_1, camera_2, camera_3, camera_4

## Usage

1. Go to the **Recordings** page from the admin dashboard
2. Find the vehicle you want to record
3. Check the RFID status (Start, Stop, Load, or Unload)
4. Click the **"Start Recording"** button
5. The system will:
   - Get the current date
   - Create a folder with today's date
   - Create a subfolder with the vehicle number
   - Create a subfolder numbered based on RFID status
   - Create folders for each camera assigned to that vehicle
6. You'll see a success message with the folder path

## Example

**Scenario:**
- Date: December 9, 2025
- Vehicle: GJ01AB1234
- RFID Status: Start (status = 1)
- Cameras: 
  - rtmp://server/live/front_cam → front_cam
  - rtmp://server/live/rear_cam → rear_cam
  - rtmp://server/live/cabin_cam → cabin_cam

**Created Structure:**
```
RECORDINGS/
└── 2025-12-09/
    └── GJ01AB1234/
        └── 1/
            ├── front_cam/
            ├── rear_cam/
            └── cabin_cam/
```

## Technical Details

### Files Modified
1. **recordings.html**
   - Added record button HTML
   - Added CSS styling for the button
   - Added JavaScript function `startRecording()`

2. **app.py**
   - Added route `/create_recording_folder`
   - Implemented folder creation logic
   - Added Firebase integration to fetch camera data

### Dependencies
- Uses Python's `Path` from `pathlib` (already imported)
- Uses `datetime` for current date (already imported)
- Uses `requests` to fetch from Firebase (already imported)
- No new dependencies required

### Security
- Only admin users can create recording folders
- Authorization check at the beginning of the route
- Error handling for all operations

## Error Handling
- Invalid device name: Returns error message
- Firebase connection failure: Returns specific error
- Folder creation failure: Returns exception message
- All errors are logged to console for debugging

## Future Enhancements
Possible improvements:
1. Add recording start/stop functionality
2. Integrate with actual video recording software
3. Add disk space check before creating folders
4. Add cleanup for old recordings
5. Add recording status indicator on the UI
