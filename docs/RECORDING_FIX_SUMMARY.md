# Recording System Fix Summary

## Date: 2025-12-11

## Issues Fixed

### 1. **Multiple Folder Creation Issue** ✅
**Problem:** When RFID status changed to 1 (Start), it was creating folder 1 AND folder 2 simultaneously instead of just folder 1.

**Root Cause:** 
- Multiple threads could execute the folder creation logic at the same time
- The folder number was determined BEFORE acquiring the lock, causing race conditions
- If two threads both checked for existing folders at the same time, they could both get the same "next folder number"

**Solution:**
- Moved the folder number determination logic INSIDE the `RECORDING_LOCK`
- The lock is now acquired BEFORE checking existing folders and creating the new folder
- This ensures only one thread can determine and create a folder at a time

**Code Change:**
```python
# OLD CODE (BUGGY):
# Find next available folder number (1, 2, 3, ...)
existing_folders = [d for d in vehicle_folder.iterdir() if d.is_dir() and d.name.isdigit()]
if existing_folders:
    folder_numbers = [int(d.name) for d in existing_folders]
    next_folder_num = max(folder_numbers) + 1
else:
    next_folder_num = 1

recording_folder = vehicle_folder / str(next_folder_num)
recording_folder.mkdir(parents=True, exist_ok=True)

# NEW CODE (FIXED):
# Acquire lock BEFORE determining folder number to prevent race conditions
with RECORDING_LOCK:
    # Find next available folder number (1, 2, 3, ...) - inside lock!
    existing_folders = [d for d in vehicle_folder.iterdir() if d.is_dir() and d.name.isdigit()]
    if existing_folders:
        folder_numbers = [int(d.name) for d in existing_folders]
        next_folder_num = max(folder_numbers) + 1
    else:
        next_folder_num = 1
    
    # Create the folder immediately while holding the lock
    recording_folder = vehicle_folder / str(next_folder_num)
    recording_folder.mkdir(parents=True, exist_ok=True)
```

---

### 2. **Looping/Duplicate Stop Error** ✅
**Problem:** When RFID status changed to 2 (Stop), the auto-stop function was being called multiple times, resulting in:
- Duplicate "AUTO-STOPPING RECORDING" messages
- Multiple attempts to stop the same cameras
- Confusing terminal output

**Root Cause:**
- The RFID monitor checks status every 2 seconds
- When a status change was detected, it would update the cache but then spawn a thread
- Before the thread could complete and remove the session, the monitor could detect the "same" status change again
- This caused multiple stop threads to be spawned for the same device

**Solution:**
- Added an `OPERATION_IN_PROGRESS` dictionary to track ongoing operations
- Before starting/stopping a recording, we set a flag (`'starting'` or `'stopping'`)
- Other threads check this flag and skip if an operation is already in progress
- The flag is cleared in a `finally` block to ensure it's always removed (even on errors)

**Code Change:**
```python
# Added tracking dictionary
OPERATION_IN_PROGRESS = {}  # {device_name: 'starting' | 'stopping'}

# OLD CODE (BUGGY):
if current_status == '2':  # Stop
    with RECORDING_LOCK:
        if device_name in RECORDING_SESSIONS:
            print(f"   ⏹️  AUTO-STOPPING RECORDING")
            threading.Thread(target=auto_stop_recording, args=(device_name,), daemon=True).start()

# NEW CODE (FIXED):
elif current_status == '2':  # Stop
    # Check if currently recording AND not already stopping
    should_stop = False
    with RECORDING_LOCK:
        if device_name in RECORDING_SESSIONS and device_name not in OPERATION_IN_PROGRESS:
            OPERATION_IN_PROGRESS[device_name] = 'stopping'
            should_stop = True
        elif device_name not in RECORDING_SESSIONS:
            print(f"   ℹ️  Not recording")
        elif device_name in OPERATION_IN_PROGRESS:
            print(f"   ℹ️  Stop operation already in progress")
    
    if should_stop:
        print(f"   ⏹️  AUTO-STOPPING RECORDING")
        threading.Thread(target=auto_stop_recording, args=(device_name,), daemon=True).start()
```

**And in the stop function:**
```python
def auto_stop_recording(device_name):
    try:
        # ... stop logic ...
    except Exception as e:
        # ... error handling ...
    finally:
        # Always clear the 'stopping' flag when done (success or failure)
        with RECORDING_LOCK:
            if device_name in OPERATION_IN_PROGRESS and OPERATION_IN_PROGRESS[device_name] == 'stopping':
                del OPERATION_IN_PROGRESS[device_name]
```

---

## Expected Behavior After Fix

### Scenario 1: First Recording Session
1. **RFID Status → 1 (Start)**
   - System creates folder `1` only
   - Starts recording cameras
   - Saves recordings to folder `1`

2. **RFID Status → 2 (Stop)**
   - System stops recording (only once)
   - Saves all recordings to folder `1`

### Scenario 2: Second Recording Session
1. **RFID Status → 1 (Start)**
   - System creates folder `2` only (not folder 1 and 2)
   - Starts recording cameras
   - Saves recordings to folder `2`

2. **RFID Status → 2 (Stop)**
   - System stops recording (only once)
   - Saves all recordings to folder `2`

### Scenario 3: Multiple Sessions
- Each start creates the next sequential folder: 1, 2, 3, 4, ...
- No duplicate folders
- No duplicate stop messages
- Clean terminal output

---

## Folder Structure
```
RECORDINGS/
└── 2025-12-11/          # Today's date
    └── GJ1A0110/        # Device name
        ├── 1/           # First recording session
        │   ├── 24_anantsurya/
        │   │   └── recording_20251211_100530.mp4
        │   └── 24_anantsurya1/
        │       └── recording_20251211_100530.mp4
        ├── 2/           # Second recording session
        │   ├── 24_anantsurya/
        │   │   └── recording_20251211_103015.mp4
        │   └── 24_anantsurya1/
        │       └── recording_20251211_103015.mp4
        └── 3/           # Third recording session
            └── ...
```

---

## Testing Recommendations

1. **Test Single Start-Stop Cycle:**
   - Change RFID to status 1
   - Wait for cameras to start
   - Verify only 1 folder is created
   - Change RFID to status 2
   - Verify only 1 stop message appears
   - Check that recordings are saved in folder 1

2. **Test Multiple Start-Stop Cycles:**
   - Repeat the above 3 times
   - Verify folders 1, 2, 3 are created sequentially
   - No gaps in folder numbers
   - No duplicate folders

3. **Test Edge Cases:**
   - Start → immediate Stop (before cameras fully start)
   - Multiple rapid status changes
   - Check that operation flags are properly cleared

---

## Technical Details

### Thread Safety Improvements
- **RECORDING_LOCK:** Ensures only one thread modifies recording sessions at a time
- **OPERATION_IN_PROGRESS:** Prevents duplicate operations from being initiated
- **finally blocks:** Guarantee that flags are always cleared, even on errors

### Performance Impact
- Minimal impact - lock is held only during critical sections
- Folder creation is still fast (milliseconds)
- No blocking during actual recording

---

## Files Modified
- `app.py` (lines 577-997)
  - Added `OPERATION_IN_PROGRESS` tracking
  - Modified `monitor_rfid_for_auto_recording()` 
  - Modified `auto_start_recording()`
  - Modified `auto_stop_recording()`
