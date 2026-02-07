# Video Recording System - Complete Documentation

## Overview
This system automatically records video from RTMP camera streams when triggered manually via the Recordings page. Videos are saved in an organized folder structure.

## How It Works

### Manual Recording Process
1. **Admin clicks "Start Recording"** on the Recordings page
2. System creates folder structure: `RECORDINGS/DATE/VEHICLE/1/CAMERA_NAME/`
3. FFmpeg starts recording from all active RTMP cameras
4. Recording continues until admin clicks "Stop Recording"
5. Videos are saved as MP4 files with timestamps

### Folder Structure
```
RECORDINGS/
└── 2025-12-09/              (Current date YYYY-MM-DD)
    └── GJ01AB1234/          (Vehicle name)
        └── 1/               (Fixed folder - recordings from Start to Stop)
            ├── front_cam/   (Camera names from RTMP URLs)
            │   └── recording_20251209_224226.mp4
            ├── rear_cam/
            │   └── recording_20251209_224226.mp4
            ├── side_cam/
            │   └── recording_20251209_224226.mp4
            └── cabin_cam/
                └── recording_20251209_224226.mp4
```

## Key Features

### 1. Only Folder "1" is Created
- **No status-based folders** (load/unload/stop folders removed)
- All recordings from Start to Stop go in folder **"1"**
- Simplified structure for easier management

### 2. Actual Video Recording
- Uses FFmpeg to record from RTMP streams
- Records all active cameras simultaneously
- Saves as MP4 format with H.264 video and AAC audio
- Filename includes timestamp for uniqueness

### 3. Live Recording Status
- **Recording indicator** with pulsing red dot
- **Live duration timer** (HH:MM:SS format)
- **Start/Stop buttons** with different colors
- Status persists across page reloads

### 4. Multi-Camera Support
- Automatically detects all RTMP cameras (rtmp1, rtmp2, rtmp3, rtmp4)
- Records from all active cameras simultaneously
- Each camera saves to its own subfolder
- Camera names extracted from RTMP URLs

## UI Components

### Start Recording Button (Red)
- Shows when no recording is active
- Gradient: Red theme
- Icon: Pulsing circle

### Stop Recording Button (Blue)
- Shows when recording is active
- Gradient: Blue theme
- Icon: Stop square

### Recording Status Banner
- Background: Light red
- Shows: "Recording: HH:MM:SS"
- Pulsing red dot indicator
- Updates every second

## Backend Routes

### 1. `/start_recording` (POST)
**Request:**
```json
{
  "device_name": "GJ01AB1234"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Started recording 4 camera(s)",
  "folder_path": "RECORDINGS/2025-12-09/GJ01AB1234/1",
  "cameras": [
    {"name": "front_cam", "status": "recording", "file": "..."},
    {"name": "rear_cam", "status": "recording", "file": "..."}
  ],
  "camera_count": 4
}
```

### 2. `/stop_recording` (POST)
**Request:**
```json
{
  "device_name": "GJ01AB1234"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Stopped recording for 4 camera(s)",
  "folder_path": "RECORDINGS/2025-12-09/GJ01AB1234/1",
  "cameras": [
    {"name": "front_cam", "file": "...", "size_mb": 145.23},
    {"name": "rear_cam", "file": "...", "size_mb": 132.45}
  ],
  "duration": "0:15:32",
  "camera_count": 4
}
```

### 3. `/get_recording_status/<device_name>` (GET)
**Response (Recording Active):**
```json
{
  "recording": true,
  "start_time": "2025-12-09T22:42:26",
  "duration": "0:05:15",
  "cameras": [...],
  "folder_path": "..."
}
```

**Response (Not Recording):**
```json
{
  "recording": false
}
```

## FFmpeg Command
```bash
ffmpeg -i rtmp://server/live/camera_name \
  -c:v copy \
  -c:a aac \
  -ar 44100 \
  -b:a 128k \
  -f mp4 \
  /path/to/output.mp4
```

**Parameters:**
- `-c:v copy`: Copy video stream (no re-encoding for performance)
- `-c:a aac`: Encode audio as AAC
- `-ar 44100`: Audio sample rate 44.1kHz
- `-b:a 128k`: Audio bitrate 128kbps
- `-f mp4`: Output format MP4

## Session Management

### Recording Sessions Storage
```python
RECORDING_SESSIONS = {
    "GJ01AB1234": {
        "start_time": "2025-12-09T22:42:26",
        "folder_path": "RECORDINGS/2025-12-09/GJ01AB1234/1",
        "processes": {
            "front_cam": {
                "process": <subprocess>,
                "output_file": "/path/to/file.mp4",
                "rtmp_url": "rtmp://..."
            },
            ...
        },
        "cameras": [...]
    }
}
```

### Thread Safety
- Uses `threading.Lock()` for concurrent access
- Prevents multiple recordings for same vehicle
- Safe process termination

## File Naming Convention
```
recording_YYYYMMDD_HHMMSS.mp4
```

**Example:**
```
recording_20251209_224226.mp4
```
- Year: 2025
- Month: 12
- Day: 09
- Hour: 22
- Minute: 42
- Second: 26

## Camera Name Extraction

RTMP URLs are parsed to extract camera names:

| RTMP URL | Extracted Name |
|----------|----------------|
| `rtmp://192.168.1.100/live/front_cam` | `front_cam` |
| `rtmp://server/stream/rear_camera` | `rear_camera` |
| `rtmp://10.0.0.5:1935/app/cam1` | `cam1` |

If extraction fails: `camera_1`, `camera_2`, etc.

## Error Handling

### 1. No Active Cameras
```
Error: No active cameras found to record
```
**Solution:** Configure RTMP URLs in Firebase

### 2. Already Recording
```
Error: Recording already in progress for this vehicle
```
**Solution:** Stop current recording first

### 3. FFmpeg Not Found
```
Error: ffmpeg command not found
```
**Solution:** Install FFmpeg and add to PATH

### 4. No Recording to Stop
```
Error: No active recording for this vehicle
```
**Solution:** Start recording first

## Status Persistence

### On Page Load
- JavaScript checks recording status for all vehicles
- If recording active: Shows stop button + live timer
- Timer syncs with actual start time
- No data lost on page refresh

### On Server Restart
- WARNING: Active recordings will be lost
- Sessions stored in memory (not persistent)
- Consider adding database persistence for production

## Performance Considerations

### CPU Usage
- Video copying (no encoding): Low CPU (~2-5% per camera)
- Audio encoding: Minimal CPU (~1% per camera)
- 4 cameras: ~10-20% CPU total

### Disk Space
- Bitrate depends on source stream
- Typical: 2-5 MB/minute per camera
- 4 cameras, 1 hour: ~500 MB - 1.2 GB
- **Monitor disk space regularly**

### Network Bandwidth
- Same as source RTMP stream
- Typical: 1-3 Mbps per camera
- 4 cameras: 4-12 Mbps total

## Best Practices

### 1. Disk Space Management
- Monitor `RECORDINGS` folder size
- Delete old recordings periodically
- Consider automatic cleanup after 30 days

### 2. Recording Duration
- Don't record for excessive periods
- Stop recording when vehicle stops
- Split long recordings into segments

### 3. Backup
- Regularly backup important recordings
- Copy to external storage
- Cloud backup for critical footage

### 4. Testing
- Test with one camera first
- Verify video quality
- Check disk space before long recordings

## Troubleshooting

### Recording Starts but No File Created
- Check FFmpeg stderr output
- Verify RTMP stream is accessible
- Check folder permissions

### Video File is 0 KB
- RTMP stream might be down
- Codec compatibility issue
- Wait for stop command (file written on stop)

### Timer Not Updating
- JavaScript error in console
- Page needs refresh
- Check browser compatibility

### Multiple Vehicles Not Working
- Thread lock issue
- Check server logs
- One vehicle at a time limitation?

## Future Enhancements

### Possible Improvements:
1. **Automatic recording** based on RFID status changes
2. **Database persistence** for recording sessions
3. **Live preview** of recordings
4. **Disk space warnings** before recording
5. **Automatic cleanup** of old recordings
6. **Recording quality** settings
7. **Cloud upload** integration
8. **Playback interface** in admin panel
9. **Recording schedule** feature
10. **Email notifications** when recording starts/stops

## Security Notes

- Only admin users can start/stop recordings
- RTMP URLs are private (not exposed to frontend)
- Recordings stored on server (not accessible via web)
- Consider adding encryption for sensitive footage

## Dependencies

- **Python**: 3.7+
- **Flask**: Web framework
- **FFmpeg**: Video recording
- **subprocess**: Process management
- **threading**: Concurrent recording
- **pathlib**: File system operations

## Installation Requirements

```bash
# Install FFmpeg (Windows)
choco install ffmpeg

# Install FFmpeg (Linux)
sudo apt-get install ffmpeg

# Install FFmpeg (macOS)
brew install ffmpeg

# Verify installation
ffmpeg -version
```

No Python package changes required - uses standard library only!
