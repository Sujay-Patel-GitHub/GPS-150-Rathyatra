# Testing Guide for Recording Folder Feature

## Prerequisites
1. Ensure you're logged in as admin
2. Have at least one registered vehicle with camera RTMP URLs configured
3. The vehicle should have an RFID status (Start, Stop, Load, or Unload)

## Test Steps

### Test 1: Basic Folder Creation
1. Navigate to `/recordings` page
2. Find a registered vehicle
3. Note the vehicle's:
   - Device name
   - RFID status
4. Click the "Start Recording" button
5. **Expected Result:**
   - Button shows "Creating Folders..." with spinner
   - Success message appears with folder path
   - Button turns green and shows "Folders Created"
   - Button resets after 3 seconds

### Test 2: Verify Folder Structure
1. After clicking record, navigate to the project directory
2. Open the `RECORDINGS` folder
3. **Expected Structure:**
   ```
   RECORDINGS/
   └── [TODAY'S DATE in YYYY-MM-DD format]/
       └── [VEHICLE NAME]/
           └── [STATUS NUMBER 1-4]/
               ├── [camera_name_1]/
               ├── [camera_name_2]/
               ├── [camera_name_3]/
               └── [camera_name_4]/
   ```

### Test 3: RFID Status Mapping
Test each RFID status:
- **RFID = Start** → Folder number should be **1**
- **RFID = Stop** → Folder number should be **2**
- **RFID = Load** → Folder number should be **3**
- **RFID = Unload** → Folder number should be **4**
- **RFID = N/A** → Folder number should be **0**

### Test 4: Camera Folder Names
1. Check Firebase for the device's RTMP URLs:
   - rtmp1, rtmp2, rtmp3, rtmp4
2. For each RTMP URL like `rtmp://server/stream/camera_front`
3. **Expected:** Folder should be named `camera_front` (last segment of URL)

### Test 5: Multiple Recordings Same Vehicle
1. Click "Start Recording" for a vehicle with RFID = Start
2. Change RFID status to "Stop" (in manage RFID)
3. Click "Start Recording" again
4. **Expected:**
   - Both folder structures exist:
     - `.../VEHICLE_NAME/1/...` (from first recording)
     - `.../VEHICLE_NAME/2/...` (from second recording)

### Test 6: Unregistered Vehicle
1. Find an unregistered vehicle
2. **Expected:**
   - "Start Recording" button is disabled
   - Button shows "Register Vehicle First"
   - Button is grayed out (opacity: 0.5)

### Test 7: Error Handling
1. Stop the Flask server
2. Try to click "Start Recording"
3. **Expected:**
   - Error message displayed
   - Button returns to original state
   - No folders created

### Test 8: Same Day Multiple Vehicles
1. Record from Vehicle A
2. Record from Vehicle B
3. **Expected Structure:**
   ```
   RECORDINGS/
   └── 2025-12-09/
       ├── VEHICLE_A/
       │   └── [status]/...
       └── VEHICLE_B/
           └── [status]/...
   ```

## Verification Checklist

- [ ] Folders are created in correct location
- [ ] Date folder is in YYYY-MM-DD format
- [ ] Vehicle folder has correct device name
- [ ] Status folder has correct number (1-4 or 0)
- [ ] Camera folders match RTMP URL names
- [ ] No errors in browser console
- [ ] No errors in Flask terminal
- [ ] Button animations work correctly
- [ ] Success/error messages display properly
- [ ] Multiple recordings don't conflict

## Common Issues and Solutions

### Issue: Folders not created
**Solution:** Check Flask terminal for errors. Ensure write permissions in project directory.

### Issue: Wrong camera names
**Solution:** Verify RTMP URLs in Firebase. Check if URLs have proper format.

### Issue: Button stays in loading state
**Solution:** Check browser console for JavaScript errors. Verify Flask server is running.

### Issue: Permission denied
**Solution:** Ensure logged in as admin. Check session storage.

## Sample Test Data

### Vehicle 1
- Device Name: `GJ01AB1234`
- RFID Status: `Start`
- Expected Folder: `RECORDINGS/2025-12-09/GJ01AB1234/1/`

### Vehicle 2
- Device Name: `MH02CD5678`
- RFID Status: `Load`
- Expected Folder: `RECORDINGS/2025-12-09/MH02CD5678/3/`

## Performance Notes
- Folder creation is fast (< 1 second typically)
- Multiple recordings can be created simultaneously
- Old recordings are NOT automatically deleted
- Disk space should be monitored manually
