from flask import Flask, request, render_template, render_template_string, redirect, url_for, abort, jsonify, \
    session, flash, send_from_directory, Response
import requests
import os
import time
import re
import subprocess
import threading
import paramiko
import json
from pathlib import Path
from datetime import datetime, timedelta

# --- IMPORT DATABASE & FIREBASE CONFIG ---
from mongodb import col_godown, col_transporters, col_drivers, col_shopkeepers, col_vehicles, col_settings, col_gps_recordings, col_map_recordings
from firebase import FIREBASE_URL, FIREBASE_KEY, LOGO_URL

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Custom logging filter to hide only live status requests
import logging

class IgnoreLiveStatusFilter(logging.Filter):
    def filter(self, record):
        # Hide only get_live_status and get_recording_status requests
        message = record.getMessage()
        return not ('/get_live_status/' in message or '/get_recording_status/' in message)

# Apply filter to werkzeug logger
log = logging.getLogger('werkzeug')
log.addFilter(IgnoreLiveStatusFilter())

@app.context_processor
def inject_common_vars():
    return {
        'logo_url': LOGO_URL,
        'username': session.get('username', 'Guest'),
        'role': session.get('role', 'User')
    }

# --- RTMP Stream Configuration ---
STREAM_ROOT = Path(__file__).parent / "streams"
STREAM_ROOT.mkdir(exist_ok=True)

PROCESS_TABLE = {}
LAST_HEARTBEAT = {}
PROCESS_LOCK = threading.Lock()


# --- TEMPLATE LOADING UTILITY ---
def get_template(name):
    # Map to new clean templates
    if name == "SHOW_DEVICES_HTML":
        filename = "show_devices_new.html"
    elif name == "GPS_VIDEO_PLAYER_HTML":
        filename = "gps_video_player.html"
    elif name == "VIEW_GPS_DETAILS_HTML":
        filename = "view_gps_details.html"
    else:
        filename = name.lower().replace('_html', '.html')
    
    # Check current directory first
    template_path = os.path.join(os.getcwd(), filename)
    
    # If not found, check templates folder
    if not os.path.exists(template_path):
        template_path = os.path.join(os.getcwd(), 'templates', filename)

    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return f"<h1>Error: Template {filename} not found</h1>"
    except Exception as e:
        return f"<h1>Error loading template {filename}: {e}</h1>"


# --- HELPER: Find User Across Collections ---
def find_user_in_db(username):
    # 1. Check Godown Managers
    u = col_godown.find_one({"username": username})
    if u: return u, "Godown Manager"
    # 2. Check Transporters
    u = col_transporters.find_one({"username": username})
    if u: return u, "Transporter"
    # 3. Check Drivers
    u = col_drivers.find_one({"username": username})
    if u: return u, "Driver"
    # 4. Check Shop Keepers
    u = col_shopkeepers.find_one({"username": username})
    if u: return u, "Shop Keeper"
    return None, None


# --- HELPER: Get Settings from Mongo ---
DEFAULT_ADD_USER_FORMAT = {
    "page_title": "Add New User",
    "section_1_title": "1. Personal Information",
    "name_label": "Full Name",
    "mobile_label": "Mobile Number",
    "email_label": "Email Address",
    "role_label": "Select Role",
    "roles_list": "Godown Manager,Transporter,Driver,Shop Keeper",
    "section_2_title": "2. Assign Devices",
    "section_3_title": "3. Credentials",
    "username_label": "Create Username",
    "password_label": "Create Password",
    "submit_btn_text": "Next Step"
}


def get_add_user_format():
    data = col_settings.find_one({"_id": "add_user_format"})
    if data:
        return data.get("format", DEFAULT_ADD_USER_FORMAT)
    return DEFAULT_ADD_USER_FORMAT


def save_add_user_format(new_fmt):
    col_settings.update_one(
        {"_id": "add_user_format"},
        {"$set": {"format": new_fmt}},
        upsert=True
    )


def get_rtmp_source():
    data = col_settings.find_one({"_id": "rtmp_source_pref"})
    if data:
        return data.get("source", "firebase")
    return "firebase"


def get_power_off_threshold():
    try:
        data = col_settings.find_one({"_id": "power_off_config"})
        if data:
            return int(data.get("minutes", 60))
    except:
        pass
    return 60

def set_power_off_threshold(minutes):
    col_settings.update_one(
        {"_id": "power_off_config"},
        {"$set": {"minutes": int(minutes)}},
        upsert=True
    )


# --- CAMERA SYSTEM HELPER FUNCTIONS ---
def safe_float(val):
    if val is None: return 0.0
    if isinstance(val, (float, int)): return float(val)
    try:
        clean_val = str(val).replace('"', '').replace("'", '').strip()
        return float(clean_val)
    except:
        return 0.0


def sanitize_data(raw_data):
    """Clean the data from Firebase to match what the frontend needs."""
    default_location = {
        'lat': 0.0, 'lng': 0.0, 'sat': 0, 'speed': 0, 'alt': 0,
        'date': '--', 'time': '--', 'UID': 'WAITING...'
    }

    if 'location' not in raw_data or not isinstance(raw_data['location'], dict):
        raw_data['location'] = default_location

    loc = raw_data['location']

    # Map firebase keys to frontend keys
    raw_lat = loc.get('latitude', loc.get('lat', 0))
    raw_lng = loc.get('longitude', loc.get('lon', loc.get('lng', 0)))

    loc['lat'] = safe_float(raw_lat)
    loc['lng'] = safe_float(raw_lng)

    if 'speed' in loc: loc['speed'] = safe_float(loc['speed'])
    if 'alt' in loc: loc['alt'] = safe_float(loc['alt'])
    if 'sat' in loc: loc['sat'] = loc['sat']

    for k, v in default_location.items():
        if k not in loc: loc[k] = v

    if 'record_config' not in raw_data:
        raw_data['record_config'] = {}

    return raw_data


def start_ffmpeg(src, out_dir):
    cmd = [
        "ffmpeg", "-fflags", "nobuffer", "-rtbufsize", "1500M",
        "-i", src, "-c:v", "copy", "-c:a", "aac", "-ar", "44100", "-b:a", "128k",
        "-f", "hls", "-hls_time", "2", "-hls_list_size", "1500", "-hls_allow_cache", "0",
        "-hls_flags", "delete_segments+append_list+omit_endlist+independent_segments",
        "-hls_segment_filename", str(out_dir / "segment_%03d.ts"),
        str(out_dir / "index.m3u8"),
    ]
    print("▶️ Starting FFmpeg:", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc


def kill_stream(sid):
    with PROCESS_LOCK:
        if sid in PROCESS_TABLE:
            proc = PROCESS_TABLE[sid]
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
            del PROCESS_TABLE[sid]
        if sid in LAST_HEARTBEAT: del LAST_HEARTBEAT[sid]
        folder = STREAM_ROOT / sid
        if folder.exists():
            try:
                for f in folder.iterdir(): f.unlink()
                folder.rmdir()
            except:
                pass


def cleanup_manager():
    while True:
        now = time.time()
        with PROCESS_LOCK:
            active_sids = list(PROCESS_TABLE.keys())
        for sid in active_sids:
            kill_needed = False
            if now - LAST_HEARTBEAT.get(sid, 0) > 15: kill_needed = True
            with PROCESS_LOCK:
                if sid in PROCESS_TABLE and PROCESS_TABLE[sid].poll() is not None: kill_needed = True
            if kill_needed: kill_stream(sid)
        time.sleep(3)


# Start cleanup thread
threading.Thread(target=cleanup_manager, daemon=True).start()


# --- TIME OFFSET HELPER ---
def apply_time_offset(dt, device_id, vehicle_doc=None):
    """Applies stored Y/M/D/H/M offsets to a datetime object."""
    try:
        if not vehicle_doc:
            vehicle_doc = col_vehicles.find_one({"device_id": device_id}) or {}
        
        offsets = vehicle_doc.get("time_offsets", {})
        if not offsets:
            # Fallback to old year_offset if exists
            y_old = vehicle_doc.get("year_offset", 0)
            if y_old:
                offsets = {'y': y_old, 'm': 0, 'd': 0, 'h': 0, 'min': 0}
            else:
                return dt

        # 1. Apply Year and Month
        y_off = int(offsets.get('y', 0))
        m_off = int(offsets.get('m', 0))
        
        if y_off != 0 or m_off != 0:
            new_year = dt.year + y_off
            new_month = dt.month + m_off
            
            # Handle month rollover
            # (new_month - 1) converts 1-12 to 0-11
            total_months = (new_month - 1)
            year_adjust = total_months // 12
            final_month = (total_months % 12) + 1
            final_year = new_year + year_adjust
            
            # Clamp day (e.g. Feb 30 -> Feb 28)
            # Simple logic: try to create date, if fail, subtract days
            try:
                dt = dt.replace(year=final_year, month=final_month)
            except ValueError:
                # Likely day out of range for new month
                # Quick fix: go to day 28 which is safe, or handle properly
                # Better: max valid day
                if final_month in [4, 6, 9, 11]: max_day = 30
                elif final_month == 2:
                    # Leap year check
                    is_leap = (final_year % 4 == 0 and final_year % 100 != 0) or (final_year % 400 == 0)
                    max_day = 29 if is_leap else 28
                else: max_day = 31
                
                dt = dt.replace(year=final_year, month=final_month, day=min(dt.day, max_day))

        # 2. Apply Day, Hour, Minute
        d_off = int(offsets.get('d', 0))
        h_off = int(offsets.get('h', 0))
        min_off = int(offsets.get('min', 0))
        
        if d_off or h_off or min_off:
            dt = dt + timedelta(days=d_off, hours=h_off, minutes=min_off)
            
        return dt
    except Exception as e:
        print(f"Error applying offset for {device_id}: {e}")
        return dt


# --- LOGIN & DASHBOARD ---
@app.route("/", methods=["GET", "POST"])
def login():
    if session.get('user_type') == 'admin':
        return redirect(url_for('admin_dashboard'))
    elif 'username' in session:
        return redirect(url_for('user_dashboard'))

    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        if username == "admin" and password == "admin":
            session['username'] = 'admin'
            session['user_type'] = 'admin'
            session['role'] = 'Administrator'
            return redirect(url_for('admin_dashboard'))

        user_doc, role_name = find_user_in_db(username)
        if user_doc and user_doc.get("password") == password:
            session['username'] = username
            session['user_type'] = 'user'
            session['role'] = role_name
            return redirect(url_for('user_dashboard'))

        flash("Invalid username or password.", "error")
        return render_template_string(get_template("LOGIN_HTML"), logo_url=LOGO_URL)

    return render_template_string(get_template("LOGIN_HTML"), logo_url=LOGO_URL)


@app.route("/admin_dashboard")
def admin_dashboard():
    if session.get('user_type') != 'admin':
        return redirect(url_for('login'))

    drivers_list = list(col_drivers.find({}, {"name": 1, "username": 1, "_id": 0}))
    transporters_list = list(col_transporters.find({}, {"name": 1, "username": 1, "_id": 0}))
    godown_managers_list = list(col_godown.find({}, {"name": 1, "username": 1, "_id": 0}))

    users_by_role = {}
    
    # Create mappings for quick phone number lookup
    driver_phone_map = {d.get("name"): d.get("mobile", "N/A") for d in col_drivers.find()}
    transporter_phone_map = {t.get("name"): t.get("mobile", "N/A") for t in col_transporters.find()}
    godown_phone_map = {g.get("name"): g.get("mobile", "N/A") for g in col_godown.find()}


    try:
        devices_url = f"{FIREBASE_URL}/data.json?auth={FIREBASE_KEY}"
        raw_firebase_data = requests.get(devices_url).json() or {}
        all_hardware_ids = list(raw_firebase_data.keys())
    except:
        all_hardware_ids = []

    registered_map = {v.get("device_id"): v for v in col_vehicles.find() if v.get("device_id")}

    devices_display_list = []
    for i, dev_id in enumerate(all_hardware_ids, start=1):
        # Get GPS data from Firebase
        gps_lat = None
        gps_lng = None
        last_updated = "N/A"
        
        location = {}
        try:
            device_firebase_data = raw_firebase_data.get(dev_id, {})
            if device_firebase_data:
                location = device_firebase_data.get("location", {})
                if location:
                    # Get coordinates
                    try:
                        gps_lat = float(location.get("lat", 0))
                        gps_lng = float(location.get("lon", 0))
                        if gps_lat == 0 and gps_lng == 0:
                            gps_lat = None
                            gps_lng = None
                    except:
                        pass
                    
            # Get timestamp and convert to IST
            last_updated_date = "N/A"
            last_updated_time = ""
            is_power_off = True
            try:
                date_str = location.get("date", "")
                time_str = location.get("time", "")
                
                if date_str and time_str:
                    utc_time = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M:%S")
                    ist_time = utc_time + timedelta(hours=5, minutes=30)
                    
                    # Apply Dynamic Offsets
                    ist_time = apply_time_offset(ist_time, dev_id, None)

                    # Check if more than configured threshold ago
                    threshold_sec = get_power_off_threshold() * 60
                    if (datetime.now() - ist_time).total_seconds() > threshold_sec:
                        is_power_off = True
                    else:
                        is_power_off = False

                    last_updated_date = ist_time.strftime("%d-%b-%Y")
                    last_updated_time = ist_time.strftime("%I:%M:%S %p")
                else:
                    is_power_off = True
            except:
                pass
        except:
            pass
        
        if dev_id in registered_map:
            info = registered_map[dev_id]
            driver_name = info.get("driver_name", "N/A") or "Pending"
            transporter_name = info.get("transporter_name", "N/A") or "N/A"
            godown_name = info.get("godown_manager", "N/A") or "N/A"
            rc_number = info.get("rc_number", "N/A") or "Not Set"
            
            # Get device raw data from Firebase
            device_raw = raw_firebase_data.get(dev_id, {})
            
            # Get camera status (mosfet)
            if "mosfet" in device_raw:
                mosfet_val = device_raw.get("mosfet")
                camera_status = "On" if str(mosfet_val) == "1" else "Off"
            else:
                camera_status = "No Data"
            
            # Check if currently recording
            with RECORDING_LOCK:
                is_recording = dev_id in RECORDING_SESSIONS

            # Get RFID / Trip Status
            rfid_data = device_raw.get("rfid_data", {})
            raw_trip_status = str(rfid_data.get("status", "0"))
            
            # Determine Trip Number (latest folder)
            latest_trip_num = 0
            try:
                base_path = Path(__file__).parent / "RECORDINGS"
                current_date_str = datetime.now().strftime("%Y-%m-%d")
                vehicle_path = base_path / current_date_str / dev_id
                
                if vehicle_path.exists():
                    session_folders = [d for d in vehicle_path.iterdir() if d.is_dir() and d.name.isdigit()]
                    if session_folders:
                        latest_trip_num = max([int(d.name) for d in session_folders])
                else:
                    date_folders = sorted([d for d in base_path.iterdir() if d.is_dir() and re.match(r'\d{4}-\d{2}-\d{2}', d.name)], reverse=True)
                    for df in date_folders:
                        vp = df / dev_id
                        if vp.exists():
                            sessions = [d for d in vp.iterdir() if d.is_dir() and d.name.isdigit()]
                            if sessions:
                                latest_trip_num = max([int(d.name) for d in sessions])
                                break
            except Exception as e:
                print(f"Error fetching trip number for {dev_id}: {e}")

            devices_display_list.append({
                "id": i, "device_name": dev_id, "is_registered": True,
                "rc_number": rc_number,
                "transporter": transporter_name,
                "transporter_phone": transporter_phone_map.get(transporter_name, "N/A") if transporter_name != "N/A" else "N/A",
                "godown_manager": godown_name,
                "godown_phone": godown_phone_map.get(godown_name, "N/A") if godown_name != "N/A" else "N/A",
                "driver_name": driver_name,
                "driver_phone": driver_phone_map.get(driver_name, "N/A") if driver_name != "Pending" else "N/A",
                "gps_lat": gps_lat,
                "gps_lng": gps_lng,
                "last_updated_date": last_updated_date,
                "last_updated_time": last_updated_time,
                "is_power_off": is_power_off,
                "camera_status": camera_status,
                "is_recording": is_recording,
                "trip_number": latest_trip_num,
                "trip_status": "Ongoing" if raw_trip_status == "1" else "Stop"
            })
        else:
            # Get device raw data from Firebase
            device_raw = raw_firebase_data.get(dev_id, {})
            
            # Get camera status (mosfet)
            if "mosfet" in device_raw:
                mosfet_val = device_raw.get("mosfet")
                camera_status = "On" if str(mosfet_val) == "1" else "Off"
            else:
                camera_status = "No Data"
            
            # Check if currently recording
            with RECORDING_LOCK:
                is_recording = dev_id in RECORDING_SESSIONS

            # Get RFID / Trip Status
            rfid_data = device_raw.get("rfid_data", {})
            raw_trip_status = str(rfid_data.get("status", "0"))
            
            # Determine Trip Number (latest folder)
            latest_trip_num = 0
            try:
                base_path = Path(__file__).parent / "RECORDINGS"
                current_date_str = datetime.now().strftime("%Y-%m-%d")
                vehicle_path = base_path / current_date_str / dev_id
                
                if vehicle_path.exists():
                    session_folders = [d for d in vehicle_path.iterdir() if d.is_dir() and d.name.isdigit()]
                    if session_folders:
                        latest_trip_num = max([int(d.name) for d in session_folders])
                else:
                    date_folders = sorted([d for d in base_path.iterdir() if d.is_dir() and re.match(r'\d{4}-\d{2}-\d{2}', d.name)], reverse=True)
                    for df in date_folders:
                        vp = df / dev_id
                        if vp.exists():
                            sessions = [d for d in vp.iterdir() if d.is_dir() and d.name.isdigit()]
                            if sessions:
                                latest_trip_num = max([int(d.name) for d in sessions])
                                break
            except Exception as e:
                print(f"Error fetching trip number for {dev_id}: {e}")

            devices_display_list.append({
                "id": i, "device_name": dev_id, "is_registered": False,
                "rc_number": "", "transporter": "", "transporter_phone": "",
                "godown_manager": "", "godown_phone": "", "driver_name": "", "driver_phone": "",
                "gps_lat": gps_lat,
                "gps_lng": gps_lng,
                "last_updated_date": last_updated_date,
                "last_updated_time": last_updated_time,
                "is_power_off": is_power_off,
                "camera_status": camera_status,
                "is_recording": is_recording,
                "trip_number": latest_trip_num,
                "trip_status": "Ongoing" if raw_trip_status == "1" else "Stop"
            })

    return render_template_string(
        get_template("SHOW_DEVICES_HTML"),
        devices=devices_display_list,
        drivers=drivers_list,
        transporters=transporters_list,
        godown_managers=godown_managers_list,
        users_by_role=users_by_role,
        logo_url=LOGO_URL
    )


@app.route("/gps_monitoring")
def gps_monitoring():
    if session.get('user_type') != 'admin':
        return redirect(url_for('login'))
    
    # Get all devices from Firebase
    try:
        devices_url = f"{FIREBASE_URL}/data.json?auth={FIREBASE_KEY}"
        raw_firebase_data = requests.get(devices_url).json() or {}
        all_device_ids = list(raw_firebase_data.keys())
    except:
        all_device_ids = []
    
    # Get all vehicles from MongoDB
    vehicles_dict = {}
    for vehicle in col_vehicles.find({}):
        vehicles_dict[vehicle.get("device_id")] = vehicle
    
    # Combine Firebase devices with MongoDB data and GPS coordinates
    vehicles_list = []
    for device_id in all_device_ids:
        vehicle_data = vehicles_dict.get(device_id, {})
        
        # Get GPS coordinates from Firebase location object
        lat = None
        lng = None
        has_gps = False
        last_updated = "N/A"
        speed = 0
        
        location = {}
        try:
            device_firebase_data = raw_firebase_data.get(device_id, {})
            if device_firebase_data:
                location = device_firebase_data.get("location", {})
                if location:
                    try:
                        lat_val = float(location.get("lat", 0))
                        lng_val = float(location.get("lon", 0))
                        # Only consider it valid GPS if both are non-zero
                        if lat_val != 0 and lng_val != 0:
                            lat = lat_val
                            lng = lng_val
                            has_gps = True
                    except:
                        pass
                    
                    # Get Speed
                    speed = location.get("speed", 0)
                
                # Get date and time from location and convert time to IST
                last_updated_date = "N/A"
                last_updated_time = ""
                is_power_off = True
                try:
                    date_str = location.get("date", "")
                    time_str = location.get("time", "")
                    
                    if date_str and time_str:
                        # Create a datetime object (UTC)
                        utc_time = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M:%S")
                        
                        # Convert to IST (UTC + 5:30)
                        ist_time = utc_time + timedelta(hours=5, minutes=30)
                        
                        # Apply Dynamic Offsets
                        vehicle_doc = vehicles_dict.get(device_id, {})
                        ist_time = apply_time_offset(ist_time, device_id, vehicle_doc)

                        # Check if more than configured threshold ago
                        threshold_sec = get_power_off_threshold() * 60
                        if (datetime.now() - ist_time).total_seconds() > threshold_sec:
                            is_power_off = True
                        else:
                            is_power_off = False

                        # Format the output
                        last_updated_date = ist_time.strftime("%d-%b-%Y")
                        last_updated_time = ist_time.strftime("%I:%M:%S %p")
                    else:
                        is_power_off = True
                except Exception as e:
                    print(f"Error converting timestamp for {device_id}: {e}")
                    pass
            else:
                is_power_off = True
        except:
            pass
        
        # Use default coordinates if no GPS
        if not has_gps:
            lat = 23.0225  # Default to Ahmedabad
            lng = 72.5714
        
        # Get camera status (mosfet)
        device_raw = raw_firebase_data.get(device_id, {})
        if "mosfet" in device_raw:
            mosfet_val = device_raw.get("mosfet")
            camera_status = "On" if str(mosfet_val) == "1" else "Off"
        else:
            camera_status = "No Data"
        
        # Get RFID / Trip Status
        rfid_data = device_raw.get("rfid_data", {})
        raw_trip_status = str(rfid_data.get("status", "0"))
        
        # Determine Trip Number (latest folder)
        latest_trip_num = 0
        try:
            base_path = Path(__file__).parent / "RECORDINGS"
            # Check today's date first, then previous dates if needed
            current_date_str = datetime.now().strftime("%Y-%m-%d")
            vehicle_path = base_path / current_date_str / device_id
            
            if vehicle_path.exists():
                session_folders = [d for d in vehicle_path.iterdir() if d.is_dir() and d.name.isdigit()]
                if session_folders:
                    latest_trip_num = max([int(d.name) for d in session_folders])
            else:
                # Try to find the most recent date folder that contains this vehicle
                date_folders = sorted([d for d in base_path.iterdir() if d.is_dir() and re.match(r'\d{4}-\d{2}-\d{2}', d.name)], reverse=True)
                for df in date_folders:
                    vp = df / device_id
                    if vp.exists():
                        sessions = [d for d in vp.iterdir() if d.is_dir() and d.name.isdigit()]
                        if sessions:
                            latest_trip_num = max([int(d.name) for d in sessions])
                            break
        except Exception as e:
            print(f"Error fetching trip number for {device_id}: {e}")
        
        # Check if currently recording
        with RECORDING_LOCK:
            is_recording = device_id in RECORDING_SESSIONS

        vehicles_list.append({
            "device_id": device_id,
            "rc_number": vehicle_data.get("rc_number"),
            "driver_name": vehicle_data.get("driver_name"),
            "transporter_name": vehicle_data.get("transporter_name"),
            "godown_manager": vehicle_data.get("godown_manager"),
            "lat": lat,
            "lng": lng,
            "has_gps": has_gps,
            "last_updated_date": last_updated_date,
            "last_updated_time": last_updated_time,
            "is_power_off": is_power_off,
            "camera_status": camera_status,
            "is_recording": is_recording,
            "trip_number": latest_trip_num,
            "trip_status": "Ongoing" if raw_trip_status == "1" else "Stop",
            "speed": speed
        })
    
    return render_template_string(
        get_template("GPS_MONITORING_HTML"),
        vehicles=vehicles_list
    )


@app.route("/get_vehicle_gps/<device_id>")
def get_vehicle_gps(device_id):
    if session.get('user_type') != 'admin':
        return jsonify({"error": "Unauthorized"}), 403
    
    try:
        # Fetch device data from Firebase
        device_url = f"{FIREBASE_URL}/data/{device_id}.json?auth={FIREBASE_KEY}"
        device_data = requests.get(device_url).json() or {}
        
        # Debug: Print available fields
        print(f"Firebase data for {device_id}: {list(device_data.keys())}")
        
        # Get location object
        location = device_data.get("location", {})
        
        # Try to get lat and lon from location object
        lat = None
        lon = None
        
        if location:
            try:
                lat = float(location.get("lat", 0))
                lon = float(location.get("lon", 0))
            except:
                pass
        
        # Get Speed
        speed = device_data.get("speed", location.get("speed", "0"))

        # Get date and time from location and convert to IST
        last_updated_date = "N/A"
        last_updated_time = ""
        is_power_off = True
        try:
            date_str = location.get("date", "")
            time_str = location.get("time", "")
            
            if date_str and time_str:
                # Parse the time (format: "HH:MM:SS" and date: "DD-MM-YYYY")
                utc_time = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M:%S")
                
                # Convert to IST (UTC + 5:30)
                ist_time = utc_time + timedelta(hours=5, minutes=30)
                
                # Apply Dynamic Offsets
                ist_time = apply_time_offset(ist_time, device_id, None)

                # Check if more than configured threshold ago
                threshold_sec = get_power_off_threshold() * 60
                if (datetime.now() - ist_time).total_seconds() > threshold_sec:
                    is_power_off = True
                else:
                    is_power_off = False

                # Format the output
                last_updated_date = ist_time.strftime("%d-%b-%Y")
                last_updated_time = ist_time.strftime("%I:%M:%S %p")
            else:
                is_power_off = True
        except Exception as e:
            print(f"Error converting timestamp for {device_id}: {e}")
            pass
        
        # Check if we have valid GPS data
        if lat is None or lon is None or (lat == 0 and lon == 0):
            return jsonify({
                "error": "No GPS data available",
                "has_gps": False,
                "lat": 23.0225,
                "lng": 72.5714,
                "speed": "0",
                "last_updated_date": last_updated_date,
                "last_updated_time": last_updated_time,
                "is_power_off": is_power_off
            })
        
        # Get camera status
        if "mosfet" in device_data:
            mosfet_val = device_data.get("mosfet")
            camera_status = "On" if str(mosfet_val) == "1" else "Off"
        else:
            camera_status = "No Data"
        
        # Check if currently recording
        with RECORDING_LOCK:
            is_recording = device_id in RECORDING_SESSIONS
        
        if is_recording:
             # Logic to save to col_gps_recordings is here but handled by separate thread usually?
             # No, if we want to record the POINT, we can do it here too if needed.
             pass

        # === NEW: Map Recording (Live View) ===
        if request.args.get('record') == 'true':
             try:
                 col_map_recordings.insert_one({
                     "device_id": device_id,
                     "lat": lat,
                     "lng": lon,
                     "speed": speed,
                     "timestamp": datetime.now(), # Server time as requested
                     "created_at": datetime.now()
                 })
             except Exception as rx:
                 print(f"Error saving map recording: {rx}")
        
        # Get RFID / Trip Status
        rfid_data = device_data.get("rfid_data", {})
        raw_trip_status = str(rfid_data.get("status", "0"))
        
        # Determine Trip Number (latest folder)
        latest_trip_num = 0
        try:
            base_path = Path(__file__).parent / "RECORDINGS"
            current_date_str = datetime.now().strftime("%Y-%m-%d")
            vehicle_path = base_path / current_date_str / device_id
            
            if vehicle_path.exists():
                session_folders = [d for d in vehicle_path.iterdir() if d.is_dir() and d.name.isdigit()]
                if session_folders:
                    latest_trip_num = max([int(d.name) for d in session_folders])
            else:
                date_folders = sorted([d for d in base_path.iterdir() if d.is_dir() and re.match(r'\d{4}-\d{2}-\d{2}', d.name)], reverse=True)
                for df in date_folders:
                    vp = df / device_id
                    if vp.exists():
                        sessions = [d for d in vp.iterdir() if d.is_dir() and d.name.isdigit()]
                        if sessions:
                            latest_trip_num = max([int(d.name) for d in sessions])
                            break
        except Exception as e:
            print(f"Error fetching trip number for {device_id}: {e}")

        return jsonify({
            "lat": lat,
            "lng": lon,
            "speed": speed,
            "has_gps": True,
            "last_updated_date": last_updated_date,
            "last_updated_time": last_updated_time,
            "is_power_off": is_power_off,
            "camera_status": camera_status,
            "is_recording": is_recording,
            "trip_number": latest_trip_num,
            "trip_status": "Ongoing" if raw_trip_status == "1" else "Stop"
        })
    except Exception as e:
        print(f"Error fetching GPS for {device_id}: {str(e)}")
        return jsonify({"error": str(e), "has_gps": False}), 500


@app.route("/get_all_vehicle_locations")
def get_all_vehicle_locations():
    if session.get('user_type') != 'admin':
        return jsonify({"error": "Unauthorized"}), 403
    
    try:
        devices_url = f"{FIREBASE_URL}/data.json?auth={FIREBASE_KEY}"
        raw_firebase_data = requests.get(devices_url).json() or {}
        return jsonify(raw_firebase_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/map_recording")
def map_recording():
    if session.get('user_type') != 'admin':
        return redirect(url_for('login'))
    return render_template_string(get_template("map_recording.html"))

@app.route("/api/get_devices_list")
def get_devices_list():
    if session.get('user_type') != 'admin':
        return jsonify([])
    
    try:
        # User wants to see ALL vehicles from Firebase (scanned/available)
        r = requests.get(f"{FIREBASE_URL}/data.json?auth={FIREBASE_KEY}&shallow=true", timeout=5)
        if r.status_code == 200:
            firebase_keys = list(r.json().keys())
            return jsonify(sorted(firebase_keys))
        else:
            # Fallback to DB if firebase fails? or empty
            return jsonify([])
    except Exception as e:
        print(f"Error fetching device list from Firebase: {e}")
        return jsonify([])

@app.route("/api/get_map_recordings_data")
def get_map_recordings_data():
    if session.get('user_type') != 'admin':
        return jsonify({"error": "Unauthorized"}), 403
        
    device_id = request.args.get('device_id')
    date_str = request.args.get('date') # YYYY-MM-DD
    
    if not device_id or not date_str:
        return jsonify({"error": "Missing params"}), 400
        
    # Query mongo with projection for speed
    try:
        start_dt = datetime.strptime(date_str, "%Y-%m-%d")
        end_dt = start_dt + timedelta(days=1)
        
        # Optimized query using the new compound index and projection
        cursor = col_map_recordings.find({
            "device_id": device_id,
            "timestamp": {"$gte": start_dt, "$lt": end_dt}
        }, {"lat": 1, "lng": 1, "speed": 1, "timestamp": 1, "_id": 0}).sort("timestamp", 1)
        
        # Check count for potential downsampling
        total_points = col_map_recordings.count_documents({
            "device_id": device_id,
            "timestamp": {"$gte": start_dt, "$lt": end_dt}
        })

        points = []
        # Downsample if excessive (e.g. > 5000 points) to speed up transfer and UI rendering
        skip_n = 1
        if total_points > 10000: skip_n = 5
        elif total_points > 5000: skip_n = 2

        count = 0
        for doc in cursor:
            count += 1
            if count % skip_n != 0: continue
            
            points.append({
                "lat": doc["lat"],
                "lng": doc["lng"],
                "speed": doc.get("speed"),
                "timestamp": doc["timestamp"].isoformat(),
                "time": doc["timestamp"].strftime("%H:%M:%S"),
                "ts": doc["timestamp"].timestamp()
            })
            
        return jsonify({"points": points, "total_raw": total_points, "optimized": skip_n > 1})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/recordings")
def recordings():
    if session.get('user_type') != 'admin':
        return redirect(url_for('login'))

    try:
        devices_url = f"{FIREBASE_URL}/data.json?auth={FIREBASE_KEY}"
        raw_firebase_data = requests.get(devices_url).json() or {}
        all_hardware_ids = list(raw_firebase_data.keys())
    except:
        all_hardware_ids = []

    registered_map = {v.get("device_id"): v for v in col_vehicles.find() if v.get("device_id")}
    
    # Status mapping: 1=Start, 2=Stop, 3=Load, 4=Unload
    status_map = {
        "1": "Start",
        "2": "Stop",
        "3": "Load",
        "4": "Unload"
    }

    devices_display_list = []
    for i, dev_id in enumerate(all_hardware_ids, start=1):
        # Get RFID data from Firebase
        rfid_status = "N/A"
        current_uid = "N/A"
        
        try:
            device_data = raw_firebase_data.get(dev_id, {})
            rfid_data = device_data.get("rfid_data", {})
            
            if rfid_data:
                # Get current UID and status
                current_uid = rfid_data.get("current", "N/A")
                status_code = str(rfid_data.get("status", ""))
                
                # Map status code to text
                if status_code in status_map:
                    rfid_status = status_map[status_code]
        except:
            pass
        
        if dev_id in registered_map:
            info = registered_map[dev_id]
            devices_display_list.append({
                "id": i, "device_name": dev_id, "is_registered": True,
                "rc_number": info.get("rc_number", "N/A"),
                "transporter": info.get("transporter_name", "N/A"),
                "godown_manager": info.get("godown_manager", "N/A"),
                "driver_name": info.get("driver_name", "N/A"),
                "rfid_status": rfid_status,
                "current_uid": current_uid
            })
        else:
            devices_display_list.append({
                "id": i, "device_name": dev_id, "is_registered": False,
                "rc_number": "", "transporter": "", "godown_manager": "", "driver_name": "",
                "rfid_status": rfid_status,
                "current_uid": current_uid
            })

    # Check if authenticated in session
    is_authenticated = session.get('admin_pages_authenticated', False)

    return render_template_string(
        get_template("RECORDINGS_HTML"),
        devices=devices_display_list,
        logo_url=LOGO_URL,
        is_authenticated=is_authenticated
    )


# API endpoint to verify password
@app.route("/verify_recordings_password", methods=["POST"])
def verify_recordings_password():
    if session.get('user_type') != 'admin':
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    data = request.get_json()
    entered_password = data.get("password", "")
    
    RECORDINGS_PASSWORD = "rushi@9945"
    
    if entered_password == RECORDINGS_PASSWORD:
        session['admin_pages_authenticated'] = True
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "Incorrect password"})


@app.route("/get_vehicle_cameras/<device_id>")
def get_vehicle_cameras(device_id):
    if session.get('user_type') != 'admin':
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    try:
        r = requests.get(f"{FIREBASE_URL}/data/{device_id}.json?auth={FIREBASE_KEY}", timeout=5)
        device_data = r.json() or {}
        
        def extract_stream_name(url, default_num):
            if not url: return None
            try:
                parts = url.strip().split('/')
                # Get the last part of the RTMP URL as the name
                name = parts[-1] if parts else f"Camera {default_num}"
                # Clean up if it's empty or just whitespace
                return name if name and name.strip() else f"Camera {default_num}"
            except:
                return f"Camera {default_num}"

        cameras = []
        for i in range(1, 5):
            url = device_data.get(f"rtmp{i}")
            if url:
                name = extract_stream_name(url, i)
                cameras.append({"id": str(i), "name": name})
        
        return jsonify({"success": True, "cameras": cameras})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# --- REMOTE & LOCAL STORAGE CONFIG ---
SSH_HOST = "103.250.160.75"
SSH_USER = "lenovo"
SSH_PASS = "p@Ss!23"
SSH_BASE_PATH = "/home/lenovo/motioncs/motionrtmp/live"
LOCAL_BASE_PATH = "/home/lenovo/motioncs/motionrtmp/live" # As requested by user

def get_ssh_client():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(SSH_HOST, username=SSH_USER, password=SSH_PASS, timeout=10)
    return client

@app.route("/server_storage")
def server_storage():
    if session.get('user_type') != 'admin':
        return redirect(url_for('login'))
    return render_template_string(get_template("SERVER_STORAGE_HTML"), logo_url=LOGO_URL)

@app.route("/api/server/list")
def api_server_list():
    if session.get('user_type') != 'admin':
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    path = request.args.get("path", "").strip("/")
    mode = request.args.get("mode", "remote") # 'remote' or 'local'
    
    base_path = SSH_BASE_PATH if mode == "remote" else LOCAL_BASE_PATH
    
    if ".." in path:
        return jsonify({"success": False, "error": "Invalid path"}), 400
    
    full_path = os.path.join(base_path, path).replace("\\", "/")
    print(f"📂 [ListDir] Path: '{path}' -> Full: '{full_path}' (Mode: {mode})")

    try:
        items = []
        if mode == "remote":
            client = get_ssh_client()
            sftp = client.open_sftp()
            try:
                for entry in sftp.listdir_attr(full_path):
                    is_dir = entry.st_mode & 0o40000 == 0o40000
                    items.append({
                        "name": entry.filename,
                        "is_dir": is_dir,
                        "size": entry.st_size if not is_dir else 0,
                        "mtime": entry.st_mtime
                    })
            finally:
                sftp.close()
                client.close()
        else:
            # Local filesystem mode
            if not os.path.exists(full_path):
                return jsonify({"success": False, "error": f"Local path not found: {full_path}"}), 404
            
            for entry in os.scandir(full_path):
                items.append({
                    "name": entry.name,
                    "is_dir": entry.is_dir(),
                    "size": entry.stat().st_size if entry.is_file() else 0,
                    "mtime": entry.stat().st_mtime
                })
        
        # Sort: directories first, then alphabetical
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        
        return jsonify({
            "success": True, 
            "items": items, 
            "current_path": path,
            "full_path_display": full_path,
            "mode": mode
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/export_recordings")
def api_export_recordings():
    if session.get('user_type') != 'admin':
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    device_id = request.args.get('vehicle')
    date_str = request.args.get('date')

    if not device_id or not date_str:
        return "Missing vehicle or date", 400

    try:
        # 1. Fetch Tracking Data from MongoDB
        start_dt = datetime.strptime(date_str, "%Y-%m-%d")
        end_dt = start_dt + timedelta(days=1)
        
        cursor = col_map_recordings.find({
            "device_id": device_id,
            "timestamp": {"$gte": start_dt, "$lt": end_dt}
        }, {"lat": 1, "lng": 1, "speed": 1, "timestamp": 1, "_id": 0}).sort("timestamp", 1)
        
        # 2. Prepare Report Data
        # Fetch Vehicle details for RC number
        vh = col_vehicles.find_one({"device_id": device_id})
        rc_number = vh.get("rc_number", "N/A") if vh else "N/A"
        
        # Get interval from request, default to 1 min
        try:
            interval_min = int(request.args.get('interval', 1))
        except:
            interval_min = 1
            
        report_rows = []
        last_recorded_time = None
        
        for doc in cursor:
            current_time = doc["timestamp"]
            
            # Filter by interval
            if last_recorded_time is None or (current_time - last_recorded_time).total_seconds() >= interval_min * 60:
                report_rows.append({
                    "Device ID": device_id,
                    "Vehicle RC": rc_number,
                    "Date": current_time.strftime("%Y-%m-%d"),
                    "Time": current_time.strftime("%H:%M:%S"),
                    "Latitude": doc["lat"],
                    "Longitude": doc["lng"],
                    "Speed (km/h)": doc.get("speed", 0)
                })
                last_recorded_time = current_time
            
        if not report_rows:
            return f"No tracking data found for {device_id} on {date_str}", 404

        # 3. Generate CSV
        import io
        import csv
        
        output = io.StringIO()
        fieldnames = ["Device ID", "Vehicle RC", "Date", "Time", "Latitude", "Longitude", "Speed (km/h)"]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(report_rows)
        
        # Return file
        output.seek(0)
        filename = f"GPS_Report_{device_id}_{date_str}.csv"
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-disposition": f"attachment; filename={filename}"}
        )

    except Exception as e:
        print(f"Error generating report: {e}")
        return f"Error generating report: {str(e)}", 500

@app.route("/api/server/delete", methods=["POST"])
def api_server_delete():
    if session.get('user_type') != 'admin':
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    data = request.json
    path = data.get("path", "").strip("/")
    name = data.get("name", "")
    is_dir = data.get("is_dir", False)
    mode = data.get("mode", "remote")
    
    if not name or ".." in path or ".." in name:
        return jsonify({"success": False, "error": "Invalid request"}), 400
    
    base_path = SSH_BASE_PATH if mode == "remote" else LOCAL_BASE_PATH
    full_path = os.path.join(base_path, path, name).replace("\\", "/")
    
    try:
        if mode == "remote":
            client = get_ssh_client()
            sftp = client.open_sftp()
            try:
                if is_dir:
                    sftp.rmdir(full_path)
                else:
                    sftp.remove(full_path)
            finally:
                sftp.close()
                client.close()
        else:
            # Local filesystem mode
            if is_dir:
                os.rmdir(full_path)
            else:
                os.remove(full_path)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/server/download")
def api_server_download():
    if session.get('user_type') != 'admin':
        return "Unauthorized", 403
    
    path = request.args.get("path", "").strip("/")
    name = request.args.get("name", "")
    mode = request.args.get("mode", "remote")
    
    if not name or ".." in path or ".." in name:
        return "Invalid request", 400
    
    base_path = SSH_BASE_PATH if mode == "remote" else LOCAL_BASE_PATH
    full_path = os.path.join(base_path, path, name).replace("\\", "/")
    
    try:
        if mode == "remote":
            client = get_ssh_client()
            sftp = client.open_sftp()
            remote_file = sftp.open(full_path, 'rb')
            
            def stream_remote():
                try:
                    while True:
                        chunk = remote_file.read(1024 * 1024)
                        if not chunk: break
                        yield chunk
                finally:
                    remote_file.close()
                    sftp.close()
                    client.close()
            return Response(stream_remote(), mimetype='application/octet-stream', headers={"Content-Disposition": f"attachment; filename={name}"})
        else:
            # Local filesystem mode
            return send_from_directory(os.path.dirname(full_path), os.path.basename(full_path), as_attachment=True)
    except Exception as e:
        return str(e), 500


# Recording session storage
RECORDING_SESSIONS = {}
RECORDING_LOCK = threading.Lock()

# RFID status tracking for auto-recording
RFID_STATUS_CACHE = {}
RFID_MONITOR_ACTIVE = False

# Flag to track if a start/stop operation is in progress for a device
OPERATION_IN_PROGRESS = {}  # {device_name: 'starting' | 'stopping'}

# GPS Recording tracking
GPS_RECORDING_THREADS = {}  # {device_name: {'thread': thread_obj, 'stop_flag': threading.Event()}}
GPS_RECORDING_LOCK = threading.Lock()

def monitor_rfid_for_auto_recording():
    """Background thread that monitors RFID status and auto-starts/stops recording"""
    global RFID_MONITOR_ACTIVE
    RFID_MONITOR_ACTIVE = True
    
    print("\n🤖 Auto-Recording Monitor Started")
    print("   Watching RFID status changes...")
    print("   Status 1 (Start) → Auto-start recording")
    print("   Status 2 (Stop) → Auto-stop recording\n")


# --- TIME THRESHOLD ROUTES ---
@app.route("/time_threshold")
def time_threshold():
    if session.get('user_type') != 'admin':
        return redirect(url_for('login'))
    
    # Check if authenticated in session
    is_authenticated = session.get('admin_pages_authenticated', False)
    
    # Get all vehicles
    vehicles = list(col_vehicles.find({}))
    
    # Get live data for raw dates
    try:
        devices_url = f"{FIREBASE_URL}/data.json?auth={FIREBASE_KEY}"
        raw_data = requests.get(devices_url).json() or {}
    except:
        raw_data = {}
        
    display_list = []
    for v in vehicles:
        dev_id = v.get("device_id")
        
        # Get stored offsets or defaults
        stored_offsets = v.get("time_offsets", {})
        if not stored_offsets and v.get("year_offset"):
            stored_offsets = {'y': v.get("year_offset"), 'm':0, 'd':0, 'h':0, 'min':0}
        
        # Ensure all keys exist for template
        final_offsets = {
            'y': stored_offsets.get('y', 0),
            'm': stored_offsets.get('m', 0),
            'd': stored_offsets.get('d', 0),
            'h': stored_offsets.get('h', 0),
            'min': stored_offsets.get('min', 0)
        }

        # Get raw date
        raw_date_str = "N/A"
        raw_time_str = "N/A"
        try:
            loc = raw_data.get(dev_id, {}).get("location", {})
            d = loc.get("date", "") # DD-MM-YYYY
            t = loc.get("time", "")
            if d:
                # Format to DD-MMM-YYYY for consistency
                dt = datetime.strptime(f"{d} {t}", "%d-%m-%Y %H:%M:%S")
                # Convert to IST
                dt_ist = dt + timedelta(hours=5, minutes=30)
                raw_date_str = dt_ist.strftime("%d-%b-%Y")
                raw_time_str = dt_ist.strftime("%I:%M:%S %p")
        except:
            pass
            
        display_list.append({
            "device_id": dev_id,
            "rc_number": v.get("rc_number"),
            "offsets": final_offsets,
            "raw_date": raw_date_str,
            "raw_time": raw_time_str
        })
        
    return render_template("time_threshold.html", vehicles=display_list, logo_url=LOGO_URL, is_authenticated=is_authenticated)


@app.route("/save_time_threshold", methods=["POST"])
def save_time_threshold():
    if session.get('user_type') != 'admin':
        return jsonify({"error": "Unauthorized"}), 403
        
    data = request.json
    dev_id = data.get("device_id")
    offsets = data.get("offsets", {})
    
    # Validation/Sanitization could go here
    
    col_vehicles.update_one(
        {"device_id": dev_id},
        {"$set": {"time_offsets": offsets}},
        upsert=True
    )
    return jsonify({"success": True})


# --- RTMP LINK MANAGEMENT ROUTES ---
@app.route("/manage_rtmp")
def manage_rtmp():
    if session.get('user_type') != 'admin':
        return redirect(url_for('login'))
    
    # Check if authenticated in session
    is_authenticated = session.get('admin_pages_authenticated', False)
    
    try:
        source = get_rtmp_source()

        # Get all registered vehicles from DB
        registered_vehicles = list(col_vehicles.find({}))
        
        # Get all device IDs from Firebase
        devices_url = f"{FIREBASE_URL}/data.json?auth={FIREBASE_KEY}"
        raw_firebase_data = requests.get(devices_url).json() or {}
        
        vehicles_display = []
        
        # We want to show all devices that exist in Firebase
        for device_id, device_data in raw_firebase_data.items():
            # Find matching registered info if any
            reg_info = next((v for v in registered_vehicles if v.get("device_id") == device_id), {})
            
            # Use per-vehicle source preference if set, otherwise use global source
            vehicle_source = reg_info.get("rtmp_source", source)
            
            # Get location and status
            location = device_data.get("location", {})
            last_updated_str = "N/A"
            is_power_off = True
            
            try:
                date_str = location.get("date", "")
                time_str = location.get("time", "")
                if date_str and time_str:
                    utc_time = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M:%S")
                    ist_time = utc_time + timedelta(hours=5, minutes=30)
                    ist_time = apply_time_offset(ist_time, device_id, None)
                    
                    threshold_sec = get_power_off_threshold() * 60
                    if (datetime.now() - ist_time).total_seconds() > threshold_sec:
                        is_power_off = True
                    else:
                        is_power_off = False
                    
                    last_updated_str = ist_time.strftime("%d-%b %I:%M %p")
                else:
                    is_power_off = True
            except:
                pass

            # Get both sets of data for the frontend to switch easily
            firebase_links = {
                "rtmp1": device_data.get("rtmp1", ""),
                "rtmp2": device_data.get("rtmp2", ""),
                "rtmp3": device_data.get("rtmp3", ""),
                "rtmp4": device_data.get("rtmp4", "")
            }
            mongo_links = reg_info.get("mongo_rtmp", {})

            # Decide which data to show as primary
            primary_data = mongo_links if vehicle_source == 'mongo' else firebase_links

            vehicles_display.append({
                "device_name": device_id,
                "rc_number": reg_info.get("rc_number", "N/A"),
                "rtmp_source": vehicle_source,
                "rtmp1": primary_data.get("rtmp1", ""),
                "rtmp2": primary_data.get("rtmp2", ""),
                "rtmp3": primary_data.get("rtmp3", ""),
                "rtmp4": primary_data.get("rtmp4", ""),
                "fb_rtmp": firebase_links,
                "mg_rtmp": mongo_links,
                "last_updated": last_updated_str,
                "is_power_off": is_power_off
            })
            
        return render_template("manage_rtmp.html", 
                               vehicles=vehicles_display, 
                               logo_url=LOGO_URL, 
                               current_source=source,
                               power_off_threshold=get_power_off_threshold(),
                               is_authenticated=is_authenticated)
        
    except Exception as e:
        print(f"Error in manage_rtmp: {e}")
        return f"Error loading RTMP Management: {e}", 500


@app.route("/save_rtmp", methods=["POST"])
def save_rtmp():
    if session.get('user_type') != 'admin':
        return jsonify({"success": False, "message": "Unauthorized"}), 403
        
    try:
        device_id = request.form.get("device_id")
        source = request.form.get("source", get_rtmp_source()) # 'firebase' or 'mongo'
        rtmp1 = request.form.get("rtmp1", "").strip()
        rtmp2 = request.form.get("rtmp2", "").strip()
        rtmp3 = request.form.get("rtmp3", "").strip()
        rtmp4 = request.form.get("rtmp4", "").strip()
        
        if not device_id:
            return jsonify({"success": False, "message": "Device ID missing"})
            
        payload = {
            "rtmp1": rtmp1,
            "rtmp2": rtmp2,
            "rtmp3": rtmp3,
            "rtmp4": rtmp4
        }

        if source == 'firebase':
            # Update Firebase
            url = f"{FIREBASE_URL}/data/{device_id}.json?auth={FIREBASE_KEY}"
            response = requests.patch(url, json=payload)
            
            # Also store preference in Mongo
            col_vehicles.update_one(
                {"device_id": device_id},
                {"$set": {"rtmp_source": "firebase"}},
                upsert=True
            )
            
            if response.status_code == 200:
                return jsonify({"success": True, "message": "RTMP links updated successfully in Firebase"})
            else:
                return jsonify({"success": False, "message": "Failed to update Firebase"})
        else:
            # Update MongoDB
            col_vehicles.update_one(
                {"device_id": device_id},
                {"$set": {
                    "mongo_rtmp": payload,
                    "rtmp_source": "mongo"
                }},
                upsert=True
            )
            return jsonify({"success": True, "message": "RTMP links updated successfully in MongoDB"})
            
    except Exception as e:
        print(f"Error saving RTMP: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/save_power_off_config", methods=["POST"])
def save_power_off_config():
    if session.get('user_type') != 'admin':
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    try:
        minutes = request.form.get("minutes")
        if minutes:
            set_power_off_threshold(minutes)
            return jsonify({"success": True, "message": "Power off threshold updated successfully"})
        return jsonify({"success": False, "message": "Missing minutes"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


def monitor_rfid_for_auto_recording():
    print("🚀 RFID Monitor Thread Started")
    while RFID_MONITOR_ACTIVE:
        try:
            # Fetch all devices from Firebase
            devices_url = f"{FIREBASE_URL}/data.json?auth={FIREBASE_KEY}"
            response = requests.get(devices_url, timeout=5)
            all_devices = response.json() or {}
            
            for device_name, device_data in all_devices.items():
                if not isinstance(device_data, dict):
                    continue
                
                # Get RFID data
                rfid_data = device_data.get('rfid_data', {})
                if not rfid_data:
                    continue
                
                # Get current status (1=Start, 2=Stop, 3=Load, 4=Unload)
                current_status = str(rfid_data.get('status', ''))
                
                # Get previous status from cache
                previous_status = RFID_STATUS_CACHE.get(device_name, '')
                
                # Detect status change
                if current_status != previous_status:
                    print(f"\n🔔 RFID STATUS CHANGE: {device_name}")
                    print(f"   Previous: {previous_status or 'None'}")
                    print(f"   Current: {current_status}")
                    
                    # Update cache FIRST to prevent duplicate detections
                    RFID_STATUS_CACHE[device_name] = current_status
                    
                    # Handle auto-recording based on status
                    if current_status == '1':  # Start
                        # Check if not already recording AND not already starting
                        should_start = False
                        with RECORDING_LOCK:
                            if device_name not in RECORDING_SESSIONS and device_name not in OPERATION_IN_PROGRESS:
                                OPERATION_IN_PROGRESS[device_name] = 'starting'
                                should_start = True
                            elif device_name in RECORDING_SESSIONS:
                                print(f"   ℹ️  Already recording")
                            elif device_name in OPERATION_IN_PROGRESS:
                                print(f"   ℹ️  Start operation already in progress")
                        
                        if should_start:
                            print(f"   ▶️  AUTO-STARTING RECORDING")
                            threading.Thread(target=auto_start_recording, args=(device_name,), daemon=True).start()
                    
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
            
        except Exception as e:
            print(f"⚠️  RFID Monitor error: {str(e)}")
        
        # Check every 2 seconds
        time.sleep(2)

def monitor_all_vehicles_gps():
    """
    Global monitoring thread to record GPS history for all vehicles
    (For Map Recording / Load Tracks feature)
    """
    print("🚀 Global GPS Monitor Thread Started")
    
    while True:
        try:
            # 1. Fetch all devices from Firebase
            # optimized: fetch entire data.json once
            try:
                r = requests.get(f"{FIREBASE_URL}/data.json?auth={FIREBASE_KEY}", timeout=10)
                all_devices = r.json() or {}
            except Exception as e:
                print(f"Global GPS Monitor Fetch Error: {e}")
                time.sleep(10)
                continue

            current_time = datetime.now()
            
            for device_id, device_data in all_devices.items():
                if not isinstance(device_data, dict): continue
                
                # Check for location data
                loc = device_data.get('location', {})
                if not loc: continue
                
                lat = loc.get('lat')
                lng = loc.get('lon') 
                speed = loc.get('speed', 0)
                
                if lat is None or lng is None: continue
                try:
                    lat = float(lat)
                    lng = float(lng)
                except: continue
                
                # Skip invalid
                if lat == 0 and lng == 0: continue
                
                # Insert into map recordings
                try:
                    col_map_recordings.insert_one({
                         "device_id": device_id,
                         "lat": lat,
                         "lng": lng,
                         "speed": speed,
                         "timestamp": current_time,
                         "created_at": current_time
                    })
                except Exception as ie:
                     # Silent fail to avoid log spam
                     pass
                
        except Exception as e:
            print(f"Global GPS Monitor Loop Error: {e}")
            
        time.sleep(5) # Poll every 5 seconds


def record_gps_data(device_name, date_str, session_number):
    """
    Background thread that continuously records GPS data from Firebase to MongoDB
    Runs while camera recording is active
    """
    stop_flag = GPS_RECORDING_THREADS[device_name]['stop_flag']
    
    print(f"\n📍 GPS RECORDING STARTED: {device_name}")
    print(f"   Session: {date_str}/{device_name}/{session_number}")
    print(f"   Polling interval: 3 seconds\n")
    
    recording_count = 0
    
    try:
        while not stop_flag.is_set():
            # Safety check: Stop if camera recording session is no longer active
            with RECORDING_LOCK:
                if device_name not in RECORDING_SESSIONS:
                    print(f"📍 GPS Safety Stop: {device_name} (no active camera session detected)")
                    break
                    
            try:
                # Fetch GPS data from Firebase
                device_url = f"{FIREBASE_URL}/data/{device_name}.json?auth={FIREBASE_KEY}"
                response = requests.get(device_url, timeout=5)
                device_data = response.json() or {}
                
                if device_data:
                    # Extract GPS and device information
                    location = device_data.get('location', {})
                    rfid_data = device_data.get('rfid_data', {})
                    
                    # Create GPS record
                    gps_record = {
                        'device_name': device_name,
                        'date': date_str,
                        'session_number': session_number,
                        'timestamp': datetime.now().isoformat(),
                        'location': {
                            'latitude': location.get('lat', 0),
                            'longitude': location.get('lon', 0),
                            'altitude': location.get('alt', 0),
                            'speed': location.get('speed', 0),
                            'satellites': location.get('sat', 0),
                            'gps_date': location.get('date', ''),
                            'gps_time': location.get('time', ''),
                            'uid': location.get('UID', '')
                        },
                        'rfid': {
                            'current': rfid_data.get('current', ''),
                            'status': rfid_data.get('status', ''),
                            'uid1': rfid_data.get('uid1', ''),
                            'uid2': rfid_data.get('uid2', ''),
                            'uid3': rfid_data.get('uid3', ''),
                            'uid4': rfid_data.get('uid4', '')
                        },
                        'device_info': {
                            'imei': device_data.get('imei', ''),
                            'venue': device_data.get('venue', ''),
                            'device_timestamp': device_data.get('timestamp', '')
                        }
                    }
                    
                    # Store in MongoDB
                    col_gps_recordings.insert_one(gps_record)
                    recording_count += 1
                    
                    # Log every 20 records to avoid clutter
                    if recording_count % 20 == 0:
                        print(f"📍 GPS: {device_name} - {recording_count} records saved")
                
            except Exception as e:
                print(f"⚠️  GPS recording error for {device_name}: {str(e)}")
            
            # Wait 3 seconds before next poll (or until stop flag is set)
            stop_flag.wait(3)
    
    except Exception as e:
        print(f"❌ GPS recording thread error for {device_name}: {str(e)}")
    finally:
        print(f"\n📍 GPS RECORDING STOPPED: {device_name}")
        print(f"   Total records saved: {recording_count}\n")
        
        # Clean up thread tracking
        with GPS_RECORDING_LOCK:
            if device_name in GPS_RECORDING_THREADS:
                del GPS_RECORDING_THREADS[device_name]

def start_gps_recording(device_name, date_str, session_number):
    """Start GPS recording thread for a device"""
    with GPS_RECORDING_LOCK:
        if device_name in GPS_RECORDING_THREADS:
            print(f"⚠️  GPS recording already active for {device_name}")
            return
        
        # Create stop flag and thread
        stop_flag = threading.Event()
        gps_thread = threading.Thread(
            target=record_gps_data,
            args=(device_name, date_str, session_number),
            daemon=True
        )
        
        GPS_RECORDING_THREADS[device_name] = {
            'thread': gps_thread,
            'stop_flag': stop_flag,
            'date': date_str,
            'session': session_number
        }
        
        gps_thread.start()

def stop_gps_recording(device_name):
    """Stop GPS recording thread for a device"""
    with GPS_RECORDING_LOCK:
        if device_name not in GPS_RECORDING_THREADS:
            print(f"ℹ️  No GPS recording active for {device_name}")
            return
        
        # Signal the thread to stop
        GPS_RECORDING_THREADS[device_name]['stop_flag'].set()
        print(f"📍 Stopping GPS recording for {device_name}...")

def auto_start_recording(device_name):
    """Auto-start recording (called from monitor thread)"""
    try:
        # Safety check: If already recording or someone else is starting, exit early
        with RECORDING_LOCK:
            if device_name in RECORDING_SESSIONS:
                print(f"WARNING: {device_name} already recording - aborting duplicate start")
                return
            if device_name in OPERATION_IN_PROGRESS and OPERATION_IN_PROGRESS[device_name] != 'starting':
                print(f"WARNING: {device_name} has operation in progress - aborting")
                return
        
        print(f"\n{'='*60}")
        print(f"AUTO-STARTING RECORDING: {device_name}")
        print(f"{'='*60}")
        print(f"Will retry every 10 seconds until cameras start")
        print(f"   (Cameras may take 1-2 minutes to power on)\n")
        
        # Get current date
        current_date = datetime.now().strftime("%Y-%m-%d")
        
        # Get camera URLs from Firebase
        r = requests.get(f"{FIREBASE_URL}/data/{device_name}.json?auth={FIREBASE_KEY}", timeout=5)
        device_data = r.json() or {}
        
        # Create base folder structure
        base_path = Path(__file__).parent / "RECORDINGS"
        vehicle_folder = base_path / current_date / device_name
        vehicle_folder.mkdir(parents=True, exist_ok=True)
        
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
        
        print(f"📁 Recording Session Folder: #{next_folder_num}")
        print(f"   Path: {recording_folder.relative_to(base_path)}\n")
        
        # Collect camera URLs
        camera_urls = {}
        for i in range(1, 5):
            rtmp_url = device_data.get(f"rtmp{i}", "")
            if rtmp_url:
                camera_name = rtmp_url.strip().split('/')[-1] or f"camera_{i}"
                camera_urls[camera_name] = rtmp_url
        
        if not camera_urls:
            print(f"❌ No camera URLs configured")
            print(f"{'='*60}\n")
            return
        
        print(f"📹 Found {len(camera_urls)} camera(s) configured\n")
        
        # Create ONE output file per camera (before retry loop)
        camera_files = {}
        for camera_name, rtmp_url in camera_urls.items():
            camera_folder = recording_folder / camera_name
            camera_folder.mkdir(parents=True, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = camera_folder / f"recording_{timestamp}.mp4"
            
            camera_files[camera_name] = {
                "output_file": output_file,
                "rtmp_url": rtmp_url,
                "folder": camera_folder
            }
        
        print(f"📁 Created output files for {len(camera_files)} camera(s)\n")
        
        # Retry logic - try FFmpeg every 10 seconds for up to 200 seconds
        max_retries = 20  # 20 * 10 seconds = 200 seconds (3.3 minutes)
        retry_count = 0
        successful_cameras = {}
        first_camera_connected = False
        
        while retry_count < max_retries:
            print(f"🔄 Retry #{retry_count + 1}/20 - Starting FFmpeg processes...")
            
            # Check RFID status - if changed to Stop (2), abort retry
            try:
                status_check = requests.get(f"{FIREBASE_URL}/data/{device_name}/rfid_data/status.json?auth={FIREBASE_KEY}", timeout=3)
                current_status = str(status_check.json() or '')
                if current_status == '2':
                    print(f"\n⚠️  RFID STATUS CHANGED TO 'STOP' - Aborting retry loop")
                    print(f"   Cancelling camera startup attempts...")
                    
                    # Kill any processes that might be starting
                    for cam_name in list(successful_cameras.keys()):
                        try:
                            proc = successful_cameras[cam_name]["process"]
                            proc.kill()
                            proc.wait(timeout=2)
                        except:
                            pass
                    successful_cameras.clear()
                    
                    # IMPORTANT: If GPS recording was started, stop it
                    stop_gps_recording(device_name)
                    
                    # IMPORTANT: Clear session data if it was created
                    with RECORDING_LOCK:
                        if device_name in RECORDING_SESSIONS:
                            del RECORDING_SESSIONS[device_name]
                    
                    print(f"   ✅ Retry loop cancelled and session cleaned up\n")
                    return
            except:
                pass  # Continue if can't check status
            
            for camera_name, cam_info in camera_files.items():
                if camera_name in successful_cameras:
                    continue  # Skip already successful cameras
                
                output_file = cam_info["output_file"]
                rtmp_url = cam_info["rtmp_url"]
                
                # FFmpeg command - using same parameters as HLS streaming
                cmd = [
                    "ffmpeg", "-y",
                    "-fflags", "nobuffer",         # No buffering (same as HLS)
                    "-rtbufsize", "1500M",         # Large RTMP buffer (same as HLS)
                    "-loglevel", "error",
                    "-i", rtmp_url,
                    "-c:v", "copy",                # Copy video codec
                    "-c:a", "aac",                 # AAC audio
                    "-ar", "44100",
                    "-b:a", "128k",
                    "-movflags", "+faststart+frag_keyframe+empty_moov",
                    "-f", "mp4",
                    str(output_file)
                ]
                
                try:
                    print(f"   🎬 Starting {camera_name}...")
                    
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        stdin=subprocess.PIPE
                    )
                    
                    # Wait 10 seconds for camera to connect and start streaming
                    print(f"      ⏳ Waiting 10 seconds to check if streaming...")
                    time.sleep(10)
                    
                    # Check if process is still running
                    if proc.poll() is None:
                        # Process is still running - check if file has any data
                        if output_file.exists():
                            file_size = output_file.stat().st_size
                            if file_size > 0:  # ANY data means FFmpeg connected!
                                successful_cameras[camera_name] = {
                                    "process": proc,
                                    "output_file": str(output_file),
                                    "rtmp_url": rtmp_url,
                                    "pid": proc.pid
                                }
                                print(f"      ✅ {camera_name} CONNECTED! (File: {file_size} bytes, PID: {proc.pid})")
                                
                                # Save to session immediately when first camera connects!
                                if not first_camera_connected:
                                    print(f"\n🎉 First camera connected - Starting recording session!")
                                    active_cameras = []
                                    for cam_name, cam_data in successful_cameras.items():
                                        active_cameras.append({
                                            "name": cam_name,
                                            "status": "recording",
                                            "file": str(Path(cam_data["output_file"]).relative_to(base_path)),
                                            "pid": cam_data["pid"]
                                        })
                                    
                                    with RECORDING_LOCK:
                                        RECORDING_SESSIONS[device_name] = {
                                            "start_time": datetime.now().isoformat(),
                                            "folder_path": str(recording_folder.relative_to(Path(__file__).parent)),
                                            "processes": successful_cameras.copy(),
                                            "cameras": active_cameras,
                                            "session_number": next_folder_num,
                                            "date": current_date
                                        }
                                    first_camera_connected = True
                                    
                                    # START GPS RECORDING when first camera connects!
                                    start_gps_recording(device_name, current_date, next_folder_num)
                                    
                                    print(f"      📹 Recording active - will continue trying other cameras...\n")
                                else:
                                    # Update existing session with new camera
                                    active_cameras = []
                                    for cam_name, cam_data in successful_cameras.items():
                                        active_cameras.append({
                                            "name": cam_name,
                                            "status": "recording",
                                            "file": str(Path(cam_data["output_file"]).relative_to(base_path)),
                                            "pid": cam_data["pid"]
                                        })
                                    
                                    with RECORDING_LOCK:
                                        RECORDING_SESSIONS[device_name]["processes"] = successful_cameras.copy()
                                        RECORDING_SESSIONS[device_name]["cameras"] = active_cameras
                                    print(f"      ➕ Added to recording session\n")
                                
                                continue
                            else:
                                print(f"      ⏳ {camera_name} file created but empty (0 bytes)")
                        else:
                            print(f"      ⏳ {camera_name} process running but no file yet")
                        
                        # Kill the process to retry
                        try:
                            proc.kill()
                            proc.wait(timeout=2)
                        except:
                            pass
                    else:
                        # Process died - camera not available
                        print(f"      ❌ {camera_name} connection failed")
                    
                    # Clean up empty files only (will retry with same filename)
                    if output_file.exists():
                        file_size = output_file.stat().st_size
                        if file_size == 0:  # Only delete truly empty files
                            output_file.unlink()
                            
                except Exception as e:
                    print(f"      ❌ {camera_name} error: {str(e)[:50]}")
            
            # Check if all cameras connected
            if len(successful_cameras) == len(camera_urls):
                print(f"\n✅ All {len(successful_cameras)} camera(s) are now recording!\n")
                break
            
            # If we have at least one camera and tried enough times, show status
            if successful_cameras and retry_count >= 10:
                print(f"\n📊 {len(successful_cameras)}/{len(camera_urls)} camera(s) recording")
                print(f"   Will keep trying remaining cameras...\n")
            
            # Continue retrying until max retries
            if retry_count < max_retries - 1 and len(successful_cameras) < len(camera_urls):
                remaining = len(camera_urls) - len(successful_cameras)
                wait_time = 10 if len(camera_urls) == 1 else 5
                print(f"   ⏳ {remaining} camera(s) still offline - waiting {wait_time}s before retry...\n")
                time.sleep(wait_time)
            
            retry_count += 1
        
        if not successful_cameras:
            print(f"❌ No cameras started streaming after {retry_count} attempts")
            print(f"   Please check:")
            print(f"   - Cameras are powered on")
            print(f"   - RTMP URLs are correct")
            print(f"   - Network connection is working")
            print(f"{'='*60}\n")
            return
        
        # Final summary
        print(f"\n📊 Final Status:")
        print(f"   ✅ Recording {len(successful_cameras)}/{len(camera_urls)} camera(s)")
        print(f"   ⏱️  Total attempts: {retry_count}")
        print(f"{'='*60}\n")
            
    except Exception as e:
        print(f"❌ Auto-start error: {str(e)}")
        import traceback
        traceback.print_exc()
        print(f"\n")
    finally:
        # Always clear the 'starting' flag when done (success or failure)
        with RECORDING_LOCK:
            if device_name in OPERATION_IN_PROGRESS and OPERATION_IN_PROGRESS[device_name] == 'starting':
                del OPERATION_IN_PROGRESS[device_name]

def auto_stop_recording(device_name):
    """Auto-stop recording (called from monitor thread)"""
    try:
        # Safety check: If not recording or someone else is stopping, exit early
        with RECORDING_LOCK:
            if device_name not in RECORDING_SESSIONS:
                print(f"WARNING: {device_name} not recording - aborting duplicate stop")
                return
            if device_name in OPERATION_IN_PROGRESS and OPERATION_IN_PROGRESS[device_name] != 'stopping':
                print(f"WARNING: {device_name} has operation in progress - aborting")
                return
        
        print(f"\n{'='*60}")
        print(f"AUTO-STOPPING RECORDING: {device_name}")
        print(f"{'='*60}")
        
        with RECORDING_LOCK:
            if device_name not in RECORDING_SESSIONS:
                print(f"ERROR: No active recording\n")
                return
            
            session_data = RECORDING_SESSIONS[device_name]
            processes = session_data["processes"]
            
            stopped_count = 0
            failed_count = 0
            
            for camera_name, camera_data in processes.items():
                proc = camera_data["process"]
                pid = camera_data.get("pid", "unknown")
                
                print(f"   🎬 Stopping {camera_name} (PID: {pid})")
                
                try:
                    # Check if process is still running
                    if proc.poll() is None:
                        # Process is running, terminate it
                        print(f"      🛑 Sending terminate signal...")
                        proc.terminate()
                        
                        # Wait for graceful shutdown
                        try:
                            proc.wait(timeout=10)
                            print(f"      ✅ Terminated gracefully")
                            stopped_count += 1
                        except subprocess.TimeoutExpired:
                            # Force kill if timeout
                            print(f"      ⚠️  Timeout - force killing...")
                            proc.kill()
                            try:
                                proc.wait(timeout=2)
                                print(f"      ⚠️  Force killed")
                                stopped_count += 1
                            except:
                                print(f"      ❌ Failed to kill")
                                failed_count += 1
                    else:
                        print(f"      ℹ️  Already stopped")
                        stopped_count += 1
                        
                    # Close all file handles
                    try:
                        if proc.stdin:
                            proc.stdin.close()
                        if proc.stdout:
                            proc.stdout.close()
                        if proc.stderr:
                            proc.stderr.close()
                    except:
                        pass
                        
                except Exception as e:
                    print(f"      ❌ Error: {str(e)}")
                    # Try force kill as last resort
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except:
                        pass
                    failed_count += 1
            
            # Calculate duration
            start_time = datetime.fromisoformat(session_data["start_time"])
            duration = datetime.now() - start_time
            duration_str = str(duration).split('.')[0]
            
            print(f"\n📊 Summary:")
            print(f"   ✅ Stopped: {stopped_count} camera(s)")
            if failed_count > 0:
                print(f"   ❌ Failed: {failed_count} camera(s)")
            print(f"   ⏱️  Duration: {duration_str}")
            
            # STOP GPS RECORDING
            stop_gps_recording(device_name)
            
            # Remove session
            del RECORDING_SESSIONS[device_name]
        
        print(f"{'='*60}\n")
        
    except Exception as e:
        print(f"❌ Auto-stop error: {str(e)}")
        import traceback
        traceback.print_exc()
        print(f"\n")
    finally:
        # Always clear the 'stopping' flag when done (success or failure)
        with RECORDING_LOCK:
            if device_name in OPERATION_IN_PROGRESS and OPERATION_IN_PROGRESS[device_name] == 'stopping':
                del OPERATION_IN_PROGRESS[device_name]

# Start RFID monitor thread only in the reloader's child process
# In debug mode, Flask creates a parent + child process. We only want the monitor
# to run in the child process to avoid duplicate monitoring.
if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    threading.Thread(target=monitor_rfid_for_auto_recording, daemon=True).start()
    threading.Thread(target=monitor_all_vehicles_gps, daemon=True).start()
else:
    # We're in the parent process (reloader process)
    # Don't start the monitor here - it will start in the child
    pass


@app.route("/start_recording", methods=["POST"])
def start_recording():
    if session.get('user_type') != 'admin':
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    
    try:
        data = request.get_json()
        device_name = data.get('device_name')
        
        if not device_name:
            return jsonify({"success": False, "message": "Device name is required"}), 400
        
        # Check if already recording
        with RECORDING_LOCK:
            if device_name in RECORDING_SESSIONS:
                return jsonify({"success": False, "message": "Recording already in progress for this vehicle"}), 400
        
        print(f"\n{'='*60}")
        print(f"🎬 STARTING RECORDING FOR: {device_name}")
        print(f"{'='*60}")
        
        # Get current date
        current_date = datetime.now().strftime("%Y-%m-%d")
        
        # Get camera URLs from Firebase
        print(f"📡 Fetching camera data from Firebase...")
        try:
            r = requests.get(f"{FIREBASE_URL}/data/{device_name}.json?auth={FIREBASE_KEY}", timeout=5)
            device_data = r.json() or {}
            print(f"✅ Firebase data retrieved")
        except Exception as e:
            print(f"❌ Firebase error: {str(e)}")
            return jsonify({"success": False, "message": f"Failed to fetch device data: {str(e)}"}), 500
        
        # Only create folder "1" for recordings (Start to Stop)
        base_path = Path(__file__).parent / "RECORDINGS"
        date_folder = base_path / current_date
        vehicle_folder = date_folder / device_name
        recording_folder = vehicle_folder / "1"
        
        print(f"📁 Recording folder: {recording_folder}")
        
        # Extract active camera URLs and test them
        active_cameras = []
        recording_processes = {}
        failed_cameras = []
        
        for i in range(1, 5):
            rtmp_key = f"rtmp{i}"
            rtmp_url = device_data.get(rtmp_key, "")
            
            if not rtmp_url:
                print(f"⚠️  Camera {i}: No RTMP URL configured")
                continue
            
            # Extract camera name from URL
            try:
                camera_name = rtmp_url.strip().split('/')[-1]
                if not camera_name:
                    camera_name = f"camera_{i}"
            except:
                camera_name = f"camera_{i}"
            
            print(f"\n📹 Camera {i} ({camera_name}):")
            print(f"   URL: {rtmp_url}")
            
            # Test camera connection with ffprobe (non-blocking)
            test_cmd = [
                "ffprobe",
                "-v", "error",
                "-rtsp_transport", "tcp",
                "-i", rtmp_url,
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1"
            ]
            
            camera_test_result = "UNKNOWN"
            try:
                print(f"   🔍 Testing connection...")
                result = subprocess.run(test_cmd, capture_output=True, timeout=5, text=True)
                if result.returncode == 0:
                    camera_test_result = "ONLINE"
                    print(f"   ✅ Camera appears to be ONLINE")
                else:
                    camera_test_result = "TEST_FAILED"
                    print(f"   ⚠️  Test failed, but will attempt recording anyway")
                    print(f"   Test error: {result.stderr[:150]}")
            except subprocess.TimeoutExpired:
                camera_test_result = "TIMEOUT"
                print(f"   ⚠️  Test timeout, but will attempt recording anyway")
            except FileNotFoundError:
                camera_test_result = "FFPROBE_NOT_FOUND"
                print(f"   ℹ️  ffprobe not available - will attempt recording anyway")
            except Exception as e:
                camera_test_result = "ERROR"
                print(f"   ⚠️  Test error ({str(e)}) - will attempt recording anyway")
            
            # Always try to record, regardless of test result
            # The actual FFmpeg process will be the real test
            
            # Create camera folder
            camera_folder = recording_folder / camera_name
            camera_folder.mkdir(parents=True, exist_ok=True)
            print(f"   📁 Created folder: {camera_folder}")
            
            # Generate unique filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = camera_folder / f"recording_{timestamp}.mp4"
            
            print(f"   💾 Output file: {output_file.name}")
            
            # Build FFmpeg command optimized for RTMP
            cmd = [
                "ffmpeg",
                "-y",  # Overwrite if exists
                "-loglevel", "error",  # Only show errors
                "-i", rtmp_url,  # Input RTMP stream
                "-c:v", "copy",  # Copy video without re-encoding
                "-c:a", "aac",  # Encode audio as AAC
                "-ar", "44100",
                "-b:a", "128k",
                "-movflags", "+faststart+frag_keyframe+empty_moov",  # Fragmented MP4 for reliability
                "-f", "mp4",
                str(output_file)
            ]
            
            try:
                print(f"   🎬 Starting FFmpeg...")
                
                # Start FFmpeg process
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.PIPE
                )
                
                print(f"   ✅ FFmpeg process started (PID: {proc.pid})")
                
                recording_processes[camera_name] = {
                    "process": proc,
                    "output_file": str(output_file),
                    "rtmp_url": rtmp_url,
                    "pid": proc.pid
                }
                
                active_cameras.append({
                    "name": camera_name,
                    "status": "recording",
                    "file": str(output_file.relative_to(base_path)),
                    "pid": proc.pid
                })
                
            except FileNotFoundError:
                print(f"   ❌ FFmpeg not found - please install FFmpeg!")
                print(f"   Download from: https://ffmpeg.org/download.html")
                failed_cameras.append(camera_name)
            except Exception as e:
                print(f"   ❌ Failed to start: {str(e)}")
                failed_cameras.append(camera_name)
        
        print(f"\n{'='*60}")
        print(f"📊 RECORDING SUMMARY:")
        print(f"   ✅ Successfully started: {len(active_cameras)} camera(s)")
        print(f"   ❌ Failed to start: {len(failed_cameras)} camera(s)")
        
        if active_cameras:
            print(f"\n🎥 Recording from:")
            for cam in active_cameras:
                print(f"   - {cam['name']} (PID: {cam['pid']})")
        
        if failed_cameras:
            print(f"\n⚠️  Failed cameras: {', '.join(failed_cameras)}")
        
        print(f"{'='*60}\n")
        
        if not recording_processes:
            return jsonify({"success": False, "message": "No cameras available to record"}), 400
        
        # Store recording session
        with RECORDING_LOCK:
            RECORDING_SESSIONS[device_name] = {
                "start_time": datetime.now().isoformat(),
                "folder_path": str(recording_folder.relative_to(Path(__file__).parent)),
                "processes": recording_processes,
                "cameras": active_cameras
            }
        
        message = f"Started recording {len(active_cameras)} camera(s)"
        if failed_cameras:
            message += f" ({len(failed_cameras)} failed)"
        
        return jsonify({
            "success": True,
            "message": message,
            "folder_path": str(recording_folder.relative_to(Path(__file__).parent)),
            "cameras": active_cameras,
            "camera_count": len(active_cameras),
            "failed_cameras": failed_cameras
        })
        
    except Exception as e:
        print(f"\n❌ ERROR IN START_RECORDING:")
        print(f"   {str(e)}")
        import traceback
        traceback.print_exc()
        print(f"{'='*60}\n")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/stop_recording", methods=["POST"])
def stop_recording():
    if session.get('user_type') != 'admin':
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    
    try:
        data = request.get_json()
        device_name = data.get('device_name')
        
        if not device_name:
            return jsonify({"success": False, "message": "Device name is required"}), 400
        
        print(f"\n{'='*60}")
        print(f"🛑 STOPPING RECORDING FOR: {device_name}")
        print(f"{'='*60}")
        
        with RECORDING_LOCK:
            if device_name not in RECORDING_SESSIONS:
                print(f"❌ No active recording found")
                print(f"{'='*60}\n")
                return jsonify({"success": False, "message": "No active recording for this vehicle"}), 400
            
            session_data = RECORDING_SESSIONS[device_name]
            processes = session_data["processes"]
            
            print(f"📹 Stopping {len(processes)} camera(s)...")
            
            # Stop all recording processes
            stopped_cameras = []
            for camera_name, camera_data in processes.items():
                proc = camera_data["process"]
                output_file = camera_data["output_file"]
                pid = camera_data.get("pid", "unknown")
                
                print(f"\n🎬 Camera: {camera_name} (PID: {pid})")
                
                try:
                    # Terminate FFmpeg
                    print(f"   🛑 Sending terminate signal...")
                    proc.terminate()
                    
                    # Wait for FFmpeg to finish
                    try:
                        proc.wait(timeout=15)
                        print(f"   ✅ Process terminated gracefully")
                    except subprocess.TimeoutExpired:
                        print(f"   ⚠️  Timeout - force killing...")
                        proc.kill()
                        proc.wait(timeout=3)
                        print(f"   ⚠️  Force killed")
                    
                    # Give file system a moment to finalize
                    import time
                    time.sleep(0.5)
                    
                    # Check if file exists and has content
                    output_path = Path(output_file)
                    if output_path.exists():
                        file_size = output_path.stat().st_size
                        
                        if file_size > 1024:  # At least 1KB
                            print(f"   📁 File created: {output_path.name}")
                            print(f"   💾 File size: {round(file_size / (1024 * 1024), 2)} MB")
                            
                            stopped_cameras.append({
                                "name": camera_name,
                                "file": str(output_file),
                                "size_mb": round(file_size / (1024 * 1024), 2)
                            })
                        else:
                            print(f"   ⚠️  File too small ({file_size} bytes) - may be corrupted")
                            print(f"   💡 Check FFmpeg log in the camera folder")
                    else:
                        print(f"   ❌ Warning: Output file not found!")
                        print(f"   💡 Check FFmpeg log in camera folder for errors")
                        print(f"   Expected: {output_path}")
                    
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                        proc.wait()
                    except:
                        pass
                    print(f"   ⚠️  Force killed after timeout")
                except Exception as e:
                    print(f"   ❌ Error: {str(e)}")
                finally:
                    # Clean up all resources
                    try:
                        if proc.stdin and not proc.stdin.closed:
                            proc.stdin.close()
                    except:
                        pass
                    try:
                        if proc.stdout and not proc.stdout.closed:
                            proc.stdout.close()
                    except:
                        pass
                    try:
                        if proc.stderr and not proc.stderr.closed:
                            proc.stderr.close()
                    except:
                        pass
            
            # Calculate recording duration
            start_time = datetime.fromisoformat(session_data["start_time"])
            duration = datetime.now() - start_time
            duration_str = str(duration).split('.')[0]  # Remove microseconds
            
            folder_path = session_data["folder_path"]
            
            # Remove session
            del RECORDING_SESSIONS[device_name]
        
        print(f"\n{'='*60}")
        print(f"📊 STOP SUMMARY:")
        print(f"   ✅ Stopped: {len(stopped_cameras)} camera(s)")
        print(f"   ⏱️  Duration: {duration_str}")
        print(f"   📁 Folder: {folder_path}")
        
        if stopped_cameras:
            print(f"\n💾 Created files:")
            for cam in stopped_cameras:
                print(f"   - {cam['name']}: {cam['size_mb']} MB")
        
        print(f"{'='*60}\n")
        
        return jsonify({
            "success": True,
            "message": f"Stopped recording for {len(stopped_cameras)} camera(s)",
            "folder_path": folder_path,
            "cameras": stopped_cameras,
            "duration": duration_str,
            "camera_count": len(stopped_cameras)
        })
        
    except Exception as e:
        print(f"\n❌ ERROR IN STOP_RECORDING:")
        print(f"   {str(e)}")
        import traceback
        traceback.print_exc()
        print(f"{'='*60}\n")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/get_recording_status/<device_name>")
def get_recording_status(device_name):
    if session.get('user_type') != 'admin':
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    
    with RECORDING_LOCK:
        if device_name in RECORDING_SESSIONS:
            session_data = RECORDING_SESSIONS[device_name]
            start_time = datetime.fromisoformat(session_data["start_time"])
            duration = datetime.now() - start_time
            
            return jsonify({
                "recording": True,
                "start_time": session_data["start_time"],
                "duration": str(duration).split('.')[0],
                "cameras": session_data["cameras"],
                "folder_path": session_data["folder_path"]
            })
        else:
            return jsonify({"recording": False})


@app.route("/get_live_status/<device_name>")
def get_live_status(device_name):
    """Get live RFID status and recording status for a device"""
    if session.get('user_type') != 'admin':
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    
    try:
        # Fetch current RFID status from Firebase
        r = requests.get(f"{FIREBASE_URL}/data/{device_name}.json?auth={FIREBASE_KEY}", timeout=3)
        device_data = r.json() or {}
        
        # Get RFID data
        rfid_data = device_data.get('rfid_data', {})
        status_num = str(rfid_data.get('status', ''))
        
        # Map status number to name
        status_map = {
            '1': 'Start',
            '2': 'Stop',
            '3': 'Load',
            '4': 'Unload'
        }
        rfid_status = status_map.get(status_num, 'N/A')
        
        # Check if currently recording
        is_recording = False
        with RECORDING_LOCK:
            is_recording = device_name in RECORDING_SESSIONS
        
        return jsonify({
            "success": True,
            "rfid_status": rfid_status,
            "rfid_status_num": status_num,
            "is_recording": is_recording
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "rfid_status": "Error",
            "is_recording": False,
            "error": str(e)
        })


@app.route("/vehicle_recordings/<vehicle_name>")
def vehicle_recordings(vehicle_name):
    """Show all recording sessions for a specific vehicle"""
    if session.get('user_type') != 'admin':
        return redirect(url_for('login'))
    
    try:
        base_path = Path(__file__).parent / "RECORDINGS"
        
        # Collect all sessions for this vehicle (ONLY from the latest date)
        sessions_data = []
        latest_date = None
        
        if base_path.exists():
            # Find the latest date folder that has this vehicle
            for date_folder in sorted(base_path.iterdir(), reverse=True):
                if not date_folder.is_dir():
                    continue
                
                vehicle_folder = date_folder / vehicle_name
                if vehicle_folder.exists():
                    latest_date = date_folder.name
                    break
            
            # Only process the latest date
            if latest_date:
                vehicle_folder = base_path / latest_date / vehicle_name
                
                # Get all session folders
                session_folders = [d for d in vehicle_folder.iterdir() 
                                 if d.is_dir() and d.name.isdigit()]
                
                for session_folder in sorted(session_folders, key=lambda x: int(x.name), reverse=True):
                    session_num = int(session_folder.name)
                    
                    # Check if currently recording this session
                    is_recording = False
                    with RECORDING_LOCK:
                        if vehicle_name in RECORDING_SESSIONS:
                            sess_data = RECORDING_SESSIONS[vehicle_name]
                            if sess_data.get('date') == latest_date and sess_data.get('session_number') == session_num:
                                is_recording = True
                    
                    # Collect videos from all cameras
                    videos = []
                    total_size = 0
                    
                    for camera_folder in sorted(session_folder.iterdir()):
                        if not camera_folder.is_dir():
                            continue
                        
                        camera_name = camera_folder.name
                        
                        for video_file in sorted(camera_folder.glob("*.mp4")):
                            file_size = video_file.stat().st_size
                            total_size += file_size
                            
                            # Extract timestamp
                            try:
                                timestamp_str = video_file.name.replace("recording_", "").replace(".mp4", "")
                                timestamp = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                                time_str = timestamp.strftime("%I:%M:%S %p")
                            except:
                                time_str = "Unknown"
                            
                            videos.append({
                                "camera": camera_name,
                                "filename": video_file.name,
                                "time": time_str,
                                "size_mb": round(file_size / (1024 * 1024), 2),
                                "download_path": f"/download_recording/{latest_date}/{vehicle_name}/{session_num}/{camera_name}/{video_file.name}"
                            })
                    
                    # Get GPS data count
                    gps_count = col_gps_recordings.count_documents({
                        "device_name": vehicle_name,
                        "date": latest_date,
                        "session_number": session_num
                    })
                    
                    sessions_data.append({
                        "date": latest_date,
                        "session_number": session_num,
                        "is_recording": is_recording,
                        "videos": videos,
                        "video_count": len(videos),
                        "gps_count": gps_count,
                        "size_mb": round(total_size / (1024 * 1024), 2)
                    })
        
        return render_template_string(
            get_template("VEHICLE_RECORDINGS_HTML"),
            vehicle_name=vehicle_name,
            sessions=sessions_data,
            logo_url=LOGO_URL
        )
        
    except Exception as e:
        print(f"Error loading vehicle recordings: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"Error: {str(e)}", 500


@app.route("/play_with_gps/<vehicle_name>/<date>/<int:session_num>/<camera_name>/<filename>")
def play_with_gps(vehicle_name, date, session_num, camera_name, filename):
    """Play video synchronized with GPS map"""
    if session.get('user_type') != 'admin':
        return redirect(url_for('login'))
    
    try:
        # Get GPS data for this session from MongoDB
        gps_records = list(col_gps_recordings.find({
            "device_name": vehicle_name,
            "date": date,
            "session_number": session_num
        }).sort("timestamp", 1))
        
        # Convert GPS records to JSON-friendly format
        gps_data = []
        for record in gps_records:
            location = record.get('location', {})
            gps_data.append({
                'lat': location.get('latitude', 0),
                'lng': location.get('longitude', 0),
                'timestamp': record.get('timestamp', ''),
                'speed': location.get('speed', 0),
                'altitude': location.get('altitude', 0)
            })
        
        # Video path
        video_path = f"/download_recording/{date}/{vehicle_name}/{session_num}/{camera_name}/{filename}"
        
        # Extract start time from filename
        try:
            timestamp_str = filename.replace("recording_", "").replace(".mp4", "")
            video_start_time = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
            video_start_str = video_start_time.isoformat()
        except:
            video_start_str = datetime.now().isoformat()
        
        return render_template_string(
            get_template("GPS_VIDEO_PLAYER_HTML"),
            vehicle_name=vehicle_name,
            camera_name=camera_name,
            date=date,
            session_num=session_num,
            video_path=video_path,
            gps_data=gps_data,
            video_start_time=video_start_str,
            total_gps_points=len(gps_data),
            logo_url=LOGO_URL
        )
        
    except Exception as e:
        print(f"Error loading GPS+video player: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"Error: {str(e)}", 500


@app.route("/download_gps/<vehicle_name>/<date>/<int:session_num>")
def download_gps(vehicle_name, date, session_num):
    """Download GPS data as a TXT file"""
    if session.get('user_type') != 'admin':
        return redirect(url_for('login'))
        
    try:
        # Try finding with both integer and string session_number for compatibility
        query = {
            "device_name": vehicle_name,
            "date": date,
            "$or": [
                {"session_number": session_num},
                {"session_number": str(session_num)}
            ]
        }
        
        gps_records = list(col_gps_recordings.find(query).sort("timestamp", 1))
        
        if not gps_records:
            # Fallback: some records might have 'vehicle_name' instead of 'device_name'
            query["vehicle_name"] = vehicle_name
            del query["device_name"]
            gps_records = list(col_gps_recordings.find(query).sort("timestamp", 1))
            
        if not gps_records:
            return f"No GPS data found for {vehicle_name} on {date} (Trip #{session_num})", 404
            
        output = "GPS LOG - PROJECT & INNOVATION LAB\n"
        output += f"Vehicle: {vehicle_name}\n"
        output += f"Date: {date}\n"
        output += f"Trip: #{session_num}\n"
        output += "="*60 + "\n"
        output += f"{'Timestamp':<25} | {'Latitude':<12} | {'Longitude':<12} | {'Speed':<8} | {'Alt':<6}\n"
        output += "-"*60 + "\n"
        
        for record in gps_records:
            loc = record.get('location', {})
            ts = record.get('timestamp', 'N/A')
            # Handle float or string/other types safely
            try:
                lat = float(loc.get('latitude', 0))
                lng = float(loc.get('longitude', 0))
                spd = float(loc.get('speed', 0))
                alt = float(loc.get('altitude', 0))
                output += f"{ts:<25} | {lat:<12.6f} | {lng:<12.6f} | {spd:<8.1f} | {alt:<6.0f}\n"
            except (ValueError, TypeError):
                output += f"{ts:<25} | {'ERROR':<12} | {'ERROR':<12} | {0:<8.1f} | {0:<6.0f}\n"
            
        filename = f"GPS_{vehicle_name}_{date}_Trip{session_num}.txt"
        return Response(
            output,
            mimetype="text/plain",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Cache-Control": "no-cache"
            }
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"System Error: {str(e)}", 500


@app.route("/view_gps_details/<vehicle_name>/<date>/<int:session_num>")
def view_gps_details(vehicle_name, date, session_num):
    """Detailed GPS view with stop analysis"""
    if session.get('user_type') != 'admin':
        return redirect(url_for('login'))
        
    try:
        query = {
            "device_name": vehicle_name,
            "date": date,
            "$or": [
                {"session_number": session_num},
                {"session_number": str(session_num)}
            ]
        }
        
        gps_records = list(col_gps_recordings.find(query).sort("timestamp", 1))
        
        if not gps_records:
            # Fallback for old/alternate field names
            query["vehicle_name"] = vehicle_name
            del query["device_name"]
            gps_records = list(col_gps_recordings.find(query).sort("timestamp", 1))
        
        gps_data = []
        for record in gps_records:
            loc = record.get('location', {})
            lat = loc.get('latitude', 0)
            lng = loc.get('longitude', 0)
            
            # Skip invalid points (0,0)
            if lat == 0 and lng == 0:
                continue
                
            gps_data.append({
                'lat': lat,
                'lng': lng,
                'timestamp': record.get('timestamp', ''),
                'speed': loc.get('speed', 0),
                'altitude': loc.get('altitude', 0)
            })
            
        # Enhanced Stop Detection
        stops = []
        if gps_data:
            current_stop = None
            # Thresholds
            MIN_STOP_DURATION = 15  # seconds
            SPEED_THRESHOLD = 3.0   # km/h
            
            def parse_ts(ts_str):
                try:
                    # Try ISO format
                    return datetime.fromisoformat(ts_str.replace(' ', 'T'))
                except:
                    try:
                        # Try common formats if ISO fails
                        return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    except:
                        return None

            for i, point in enumerate(gps_data):
                speed = point.get('speed', 0)
                try: speed = float(speed)
                except: speed = 0
                
                if speed < SPEED_THRESHOLD:
                    if current_stop is None:
                        current_stop = {
                            'start_time': point['timestamp'],
                            'end_time': point['timestamp'],
                            'lat': point['lat'],
                            'lng': point['lng'],
                            'points_count': 1,
                            'speeds': [speed]
                        }
                    else:
                        current_stop['end_time'] = point['timestamp']
                        current_stop['points_count'] += 1
                        current_stop['speeds'].append(speed)
                else:
                    if current_stop:
                        t1 = parse_ts(current_stop['start_time'])
                        t2 = parse_ts(current_stop['end_time'])
                        
                        if t1 and t2:
                            duration = (t2 - t1).total_seconds()
                            if duration >= MIN_STOP_DURATION:
                                current_stop['duration_sec'] = int(duration)
                                # Always use 2 decimal places for lat/lng in stats
                                current_stop['lat_display'] = round(current_stop['lat'], 6)
                                current_stop['lng_display'] = round(current_stop['lng'], 6)
                                
                                hours, remainder = divmod(int(duration), 3600)
                                mins, secs = divmod(remainder, 60)
                                if hours > 0:
                                    current_stop['duration_text'] = f"{hours}h {mins}m {secs}s"
                                else:
                                    current_stop['duration_text'] = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
                                
                                stops.append(current_stop)
                        current_stop = None
            
            # Final stop check for live/currently parked vehicles
            if current_stop:
                t1 = parse_ts(current_stop['start_time'])
                t2 = parse_ts(current_stop['end_time'])
                if t1 and t2:
                    duration = (t2 - t1).total_seconds()
                    # If it's a very long stop (like live parked), always show it even if it hasn't "ended"
                    if duration >= MIN_STOP_DURATION:
                        current_stop['duration_sec'] = int(duration)
                        hours, remainder = divmod(int(duration), 3600)
                        mins, secs = divmod(remainder, 60)
                        current_stop['duration_text'] = f"{hours}h {mins}m {secs}s" if hours > 0 else (f"{mins}m {secs}s" if mins > 0 else f"{secs}s")
                        stops.append(current_stop)

        # Dwell Analysis: Group stops by location (within ~100m)
        dwell_summary = {}
        for stop in stops:
            # Round coordinates to 3 decimal places (~110m cluster)
            key = (round(stop['lat'], 3), round(stop['lng'], 3))
            if key not in dwell_summary:
                dwell_summary[key] = {
                    'lat': stop['lat'],
                    'lng': stop['lng'],
                    'total_duration_sec': 0,
                    'visit_count': 0,
                    'last_timestamp': stop['start_time']
                }
            dwell_summary[key]['total_duration_sec'] += stop['duration_sec']
            dwell_summary[key]['visit_count'] += 1
            
        dwell_list = []
        for key, data in dwell_summary.items():
            hours, remainder = divmod(data['total_duration_sec'], 3600)
            mins, secs = divmod(remainder, 60)
            text = []
            if hours > 0: text.append(f"{hours}h")
            if mins > 0: text.append(f"{mins}m")
            if not text: text.append(f"{secs}s")
            data['total_duration_text'] = " ".join(text)
            dwell_list.append(data)
            
        dwell_list.sort(key=lambda x: x['total_duration_sec'], reverse=True)

        return render_template_string(
            get_template("VIEW_GPS_DETAILS_HTML"),
            vehicle_name=vehicle_name,
            date=date,
            session_num=session_num,
            gps_data=gps_data,
            stops=stops,
            dwell_list=dwell_list[:5], 
            total_points=len(gps_data),
            logo_url=LOGO_URL
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return str(e), 500

@app.route("/download_recording/<path:file_path>")
def download_recording(file_path):
    if session.get('user_type') != 'admin':
        return "Unauthorized", 403
    
    try:
        base_path = Path(__file__).parent / "RECORDINGS"
        full_path = base_path / file_path
        
        # Security check - ensure file is within RECORDINGS folder
        if not str(full_path.resolve()).startswith(str(base_path.resolve())):
            return "Access denied", 403
        
        if not full_path.exists():
            return "File not found", 404
            
        # Check if user wants to download as attachment
        as_attachment = request.args.get('download', '0') == '1'
        
        return send_from_directory(
            full_path.parent,
            full_path.name,
            as_attachment=as_attachment,
            mimetype='video/mp4'
        )
    except Exception as e:
        print(f"Error serving recording: {str(e)}")
        return str(e), 500


@app.route("/delete_recording/<path:file_path>", methods=["POST"])
def delete_recording(file_path):
    if session.get('user_type') != 'admin':
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    
    try:
        base_path = Path(__file__).parent / "RECORDINGS"
        full_path = base_path / file_path
        
        # Security check
        if not str(full_path.resolve()).startswith(str(base_path.resolve())):
            return jsonify({"success": False, "message": "Access denied"}), 403
        
        if full_path.exists():
            file_size = full_path.stat().st_size
            full_path.unlink()
            
            return jsonify({
                "success": True,
                "message": f"Deleted {full_path.name}",
                "size_mb": round(file_size / (1024 * 1024), 2)
            })
        else:
            return jsonify({"success": False, "message": "File not found"}), 404
            
    except Exception as e:
        print(f"Error deleting recording: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/list_roles")
def list_roles():
    if session.get('user_type') != 'admin': return redirect(url_for('login'))
    roles_data = {
        "Driver": [{"name": u.get('name', 'Unknown'), "username": u.get('username')} for u in col_drivers.find()],
        "Transporter": [{"name": u.get('name', 'Unknown'), "username": u.get('username')} for u in
                        col_transporters.find()],
        "Shop Keeper": [{"name": u.get('name', 'Unknown'), "username": u.get('username')} for u in
                        col_shopkeepers.find()],
        "Godown Manager": [{"name": u.get('name', 'Unknown'), "username": u.get('username')} for u in col_godown.find()]
    }
    return render_template_string(get_template("LIST_ROLES_HTML"), roles_data=roles_data, logo_url=LOGO_URL)


@app.route("/get_user_details/<role>/<username>")
def get_user_details(role, username):
    if session.get('user_type') != 'admin': return jsonify({"error": "Unauthorized"}), 403

    collection = None
    if role == 'Driver':
        collection = col_drivers
    elif role == 'Transporter':
        collection = col_transporters
    elif role == 'Shop Keeper':
        collection = col_shopkeepers
    elif role == 'Godown Manager':
        collection = col_godown

    if collection is not None:
        user = collection.find_one({"username": username}, {"_id": 0})
        if user: return jsonify(user)

    return jsonify({"error": "User not found"}), 404


@app.route("/delete_user/<role>/<username>", methods=["DELETE"])
def delete_user(role, username):
    if session.get('user_type') != 'admin':
        return jsonify({"error": "Unauthorized"}), 403

    collection = None
    if role == 'Driver':
        collection = col_drivers
    elif role == 'Transporter':
        collection = col_transporters
    elif role == 'Shop Keeper':
        collection = col_shopkeepers
    elif role == 'Godown Manager':
        collection = col_godown
    else:
        return jsonify({"error": "Invalid role"}), 400

    if collection is None:
        return jsonify({"error": "Invalid role"}), 400

    # Check if user exists
    user = collection.find_one({"username": username})
    if not user:
        return jsonify({"error": "User not found"}), 404

    try:
        # Delete the user from the collection
        result = collection.delete_one({"username": username})
        
        if result.deleted_count > 0:
            # Additionally, de-register any vehicles associated with this user
            if role == 'Driver':
                col_vehicles.update_many(
                    {"driver_name": user.get("name")},
                    {"$unset": {"driver_name": "", "driver_username": ""}}
                )
            elif role == 'Transporter':
                col_vehicles.update_many(
                    {"transporter_name": user.get("name")},
                    {"$unset": {"transporter_name": "", "transporter_username": ""}}
                )
            elif role == 'Godown Manager':
                col_vehicles.update_many(
                    {"godown_manager": user.get("name")},
                    {"$unset": {"godown_manager": "", "godown_username": ""}}
                )
            
            return jsonify({"success": True, "message": f"User {username} deleted successfully"})
        else:
            return jsonify({"error": "Failed to delete user"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/update_user_details", methods=["POST"])
def update_user_details():
    if session.get('user_type') != 'admin': return jsonify({"error": "Unauthorized"}), 403

    data = request.json
    role = data.get("role")
    old_username = data.get("username")
    updates = data.get("updates")

    if not role or not old_username or not updates:
        return jsonify({"error": "Missing required fields"}), 400

    collection = None
    if role == 'Driver':
        collection = col_drivers
    elif role == 'Transporter':
        collection = col_transporters
    elif role == 'Shop Keeper':
        collection = col_shopkeepers
    elif role == 'Godown Manager':
        collection = col_godown

    if collection is None:
        return jsonify({"error": "Invalid role"}), 400

    # Check if username is being changed
    new_username = updates.get("username")
    username_changed = new_username and new_username != old_username

    if username_changed:
        # Check if new username already exists
        existing_user, _ = find_user_in_db(new_username)
        if existing_user:
            return jsonify({"error": f"Username '{new_username}' already exists"}), 400

        # Get the old document
        old_doc = collection.find_one({"username": old_username})
        if not old_doc:
            return jsonify({"error": "User not found"}), 404

        # Create new document with updated data
        new_doc = old_doc.copy()
        new_doc.update(updates)
        
        # Remove the _id field to avoid duplicate key error
        if '_id' in new_doc:
            del new_doc['_id']
        
        # Insert new document (MongoDB will generate a new _id)
        collection.insert_one(new_doc)
        
        # Delete old document
        collection.delete_one({"username": old_username})
        
        # Update references in vehicles collection if applicable
        if role == 'Driver':
            col_vehicles.update_many(
                {"driver_name": old_doc.get("name")},
                {"$set": {"driver_username": new_username}}
            )
        elif role == 'Transporter':
            col_vehicles.update_many(
                {"transporter_name": old_doc.get("name")},
                {"$set": {"transporter_username": new_username}}
            )
        elif role == 'Godown Manager':
            col_vehicles.update_many(
                {"godown_manager": old_doc.get("name")},
                {"$set": {"godown_username": new_username}}
            )
        
        return jsonify({"success": True, "message": "User details updated successfully", "username_changed": True, "new_username": new_username})
    else:
        # Normal update without username change
        result = collection.update_one({"username": old_username}, {"$set": updates})

        if result.matched_count == 0:
            return jsonify({"error": "User not found"}), 404

        return jsonify({"success": True, "message": "User details updated successfully", "username_changed": False})


@app.route("/save_vehicle_details", methods=["POST"])
def save_vehicle_details():
    if session.get('user_type') != 'admin': return redirect(url_for('login'))

    device_id = request.form.get("device_id")
    transporter_name = request.form.get("transporter_name")
    godown_manager = request.form.get("godown_manager")
    force_assign = request.form.get("force_assign")  # For confirmation override

    if device_id and transporter_name and godown_manager:
        # Check if transporter is already assigned to another vehicle
        existing_assignment = col_vehicles.find_one({
            "transporter_name": transporter_name,
            "device_id": {"$ne": device_id}  # Different device
        })
        
        if existing_assignment:
            existing_godown = existing_assignment.get("godown_manager")
            
            # If different godown manager and not forced, block with message
            if existing_godown != godown_manager and force_assign != "true":
                # Return error with special flag for frontend to show confirmation
                flash(f"WARNING: Transporter '{transporter_name}' is already assigned by Godown Manager '{existing_godown}' to device '{existing_assignment.get('device_id')}'. Click 'Continue' to override.", "warning_confirm")
                # Store the attempted assignment in session for retry
                session['pending_assignment'] = {
                    'device_id': device_id,
                    'transporter_name': transporter_name,
                    'godown_manager': godown_manager
                }
                return redirect(url_for('admin_dashboard'))

        # Clear pending assignment from session
        session.pop('pending_assignment', None)

        col_vehicles.update_one(
            {"device_id": device_id},
            {"$set": {
                "device_id": device_id,
                "transporter_name": transporter_name,
                "godown_manager": godown_manager,
                "driver_name": "",
                "rc_number": "",
                "assigned_at": time.time(),
                "updated_by": session.get("username")
            }}, upsert=True
        )
        flash(f"Vehicle {device_id} assigned to {transporter_name} (Godown: {godown_manager}). Driver can be assigned next.", "success")
    else:
        flash("Missing details (Device ID, Transporter, or Godown Manager).", "error")
    return redirect(url_for('admin_dashboard'))


@app.route("/deregister_vehicle", methods=["POST"])
def deregister_vehicle():
    if session.get('user_type') != 'admin':
        return redirect(url_for('login'))

    device_id = request.form.get("device_id")

    if device_id:
        # Delete the record entirely to remove RC number, Transporter name, and reset status to Unregistered
        result = col_vehicles.delete_one({"device_id": device_id})
        
        if result.deleted_count > 0:
            flash(f"Device {device_id} deregistered successfully. RC and Transporter removed.", "success")
        else:
            flash(f"Device {device_id} was not found or already unregistered.", "error")
    else:
        flash("Invalid request: No Device ID provided.", "error")

    return redirect(url_for('admin_dashboard'))


@app.route("/transporter_assign_driver", methods=["POST"])
def transporter_assign_driver():
    if 'username' not in session or session.get('role') != 'Transporter':
        return redirect(url_for('login'))

    device_id = request.form.get("device_id")
    driver_name = request.form.get("driver_name")

    if not device_id or not driver_name:
        flash("Missing device or driver selection.", "error")
        return redirect(url_for('user_dashboard'))

    user_doc, _ = find_user_in_db(session['username'])
    my_name = user_doc.get("name")

    vehicle = col_vehicles.find_one({"device_id": device_id, "transporter_name": my_name})

    if vehicle:
        col_vehicles.update_one(
            {"device_id": device_id},
            {"$set": {"driver_name": driver_name}}
        )
        flash(f"Driver {driver_name} successfully assigned to {device_id}.", "success")
    else:
        flash("Permission denied or vehicle not found in your fleet.", "error")

    return redirect(url_for('user_dashboard'))


@app.route("/transporter_register_vehicle", methods=["POST"])
def transporter_register_vehicle():
    if 'username' not in session or session.get('role') != 'Transporter':
        return redirect(url_for('login'))

    device_id = request.form.get("device_id")
    rc_number = request.form.get("rc_number")
    driver_name = request.form.get("driver_name")

    if not device_id or not rc_number or not driver_name:
        flash("Missing required information (RC number or driver).", "error")
        return redirect(url_for('user_dashboard'))

    user_doc, _ = find_user_in_db(session['username'])
    my_name = user_doc.get("name")

    # Verify this vehicle is assigned to this transporter
    vehicle = col_vehicles.find_one({"device_id": device_id, "transporter_name": my_name})

    if vehicle:
        # Update the vehicle with RC number and driver
        col_vehicles.update_one(
            {"device_id": device_id},
            {"$set": {
                "rc_number": rc_number,
                "driver_name": driver_name,
                "registered_at": time.time(),
                "registered_by": session.get("username")
            }}
        )
        flash(f"Vehicle {device_id} registered successfully with RC: {rc_number} and Driver: {driver_name}.", "success")
    else:
        flash("Permission denied or vehicle not found in your fleet.", "error")

    return redirect(url_for('user_dashboard'))


@app.route("/transporter_update_vehicle", methods=["POST"])
def transporter_update_vehicle():
    if 'username' not in session or session.get('role') != 'Transporter':
        return redirect(url_for('login'))

    device_id = request.form.get("device_id")
    rc_number = request.form.get("rc_number")
    driver_name = request.form.get("driver_name")

    if not device_id or not rc_number or not driver_name:
        flash("Missing required information (RC number or driver).", "error")
        return redirect(url_for('user_dashboard'))

    user_doc, _ = find_user_in_db(session['username'])
    my_name = user_doc.get("name")

    # Verify this vehicle is assigned to this transporter
    vehicle = col_vehicles.find_one({"device_id": device_id, "transporter_name": my_name})

    if vehicle:
        # Update the vehicle with new RC number and driver
        col_vehicles.update_one(
            {"device_id": device_id},
            {"$set": {
                "rc_number": rc_number,
                "driver_name": driver_name,
                "updated_at": time.time(),
                "updated_by": session.get("username")
            }}
        )
        flash(f"Vehicle {device_id} updated successfully. RC: {rc_number}, Driver: {driver_name}.", "success")
    else:
        flash("Permission denied or vehicle not found in your fleet.", "error")

    return redirect(url_for('user_dashboard'))


@app.route("/user_dashboard")
def user_dashboard():
    if 'username' not in session: return redirect(url_for('login'))
    username = session['username']
    role = session.get('role', 'User')
    user_doc, _ = find_user_in_db(username)
    if not user_doc: session.clear(); return redirect(url_for('login'))

    assigned_vehicles = []
    drivers_list = []

    if role == 'Driver':
        v = col_vehicles.find_one({"driver_name": user_doc.get("name")})
        if v: assigned_vehicles.append({
            "device_name": v.get("device_id"),
            "rc_number": v.get("rc_number"),
            "transporter": v.get("transporter_name"),
            "is_camera_allowed": False,
            "driver_name": user_doc.get("name")
        })

    elif role == 'Transporter':
        drivers_cursor = col_drivers.find({}, {"name": 1, "username": 1, "_id": 0})
        drivers_list = list(drivers_cursor)

        for v in col_vehicles.find({"transporter_name": user_doc.get("name")}):
            assigned_vehicles.append({
                "device_name": v.get("device_id"),
                "rc_number": v.get("rc_number"),
                "transporter": "My Fleet",
                "driver_name": v.get("driver_name", "Unassigned"),
                "is_camera_allowed": False
            })

    elif role == 'Godown Manager':
        for v in col_vehicles.find({"godown_manager": user_doc.get("name")}):
            assigned_vehicles.append({
                "device_name": v.get("device_id"),
                "rc_number": v.get("rc_number", "N/A"),
                "transporter": v.get("transporter_name", "N/A"),
                "driver_name": v.get("driver_name", "Unassigned"),
                "is_camera_allowed": False
            })

    # Use different template for Transporter
    if role == 'Transporter':
        return render_template_string(
            get_template("transporter_dashboard.html"),
            username=username,
            devices=assigned_vehicles,
            drivers=drivers_list,
            logo_url=LOGO_URL
        )
    else:
        return render_template_string(
            get_template("USER_DASHBOARD_HTML"),
            username=username,
            role=role,
            devices=assigned_vehicles,
            drivers=drivers_list,
            logo_url=LOGO_URL
        )


@app.route("/format_add_user", methods=["GET", "POST"])
def format_add_user():
    if session.get('user_type') != 'admin': return redirect(url_for('login'))
    if request.method == "POST":
        new_format = {
            "page_title": request.form.get("page_title"),
            "section_1_title": request.form.get("section_1_title"),
            "name_label": request.form.get("name_label"),
            "mobile_label": request.form.get("mobile_label"),
            "email_label": request.form.get("email_label"),
            "role_label": request.form.get("role_label"),
            "roles_list": request.form.get("roles_list"),
            "section_2_title": request.form.get("section_2_title"),
            "section_3_title": request.form.get("section_3_title"),
            "username_label": request.form.get("username_label"),
            "password_label": request.form.get("password_label"),
            "submit_btn_text": request.form.get("submit_btn_text"),
        }
        save_add_user_format(new_format)
        flash("Format saved!", "success")
        return redirect(url_for('format_add_user'))
    return render_template_string(get_template("FORMAT_ADD_USER_HTML"), fmt=get_add_user_format(), logo_url=LOGO_URL)


@app.route("/add_user", methods=["GET", "POST"])
def add_user():
    if session.get('user_type') != 'admin': return redirect(url_for('login'))

    fmt = get_add_user_format()
    roles_list = [r.strip() for r in fmt.get('roles_list', '').split(',') if r.strip()]

    if request.method == "POST":
        username = request.form.get("username")
        existing, _ = find_user_in_db(username)
        if existing:
            return render_template_string(get_template("ADD_USER_STEP_1_HTML"), fmt=fmt, roles_list=roles_list,
                                          error_message="Username already exists.", logo_url=LOGO_URL)

        temp_user = {
            "username": username,
            "password": request.form.get("password"),
            "role": request.form.get("role"),
            "name": request.form.get("name"),
            "mobile": request.form.get("mobile"),
            "email": request.form.get("email"),
            "step_completed": 1,
            "setup_complete": False,
            "created_at": time.time()
        }

        session['temp_user_data'] = temp_user

        role = temp_user['role']
        if role == "Driver":
            return redirect(url_for('add_user_driver', username=username))
        elif role == "Transporter":
            return redirect(url_for('add_user_transporter', username=username))
        elif role == "Shop Keeper":
            return redirect(url_for('add_user_fps', username=username))
        elif role == "Godown Manager":
            return redirect(url_for('add_user_godown', username=username))
        else:
            flash("Unknown Role.", "error")

    return render_template_string(get_template("ADD_USER_STEP_1_HTML"), fmt=fmt, roles_list=roles_list,
                                  error_message=None, logo_url=LOGO_URL)


@app.route("/add_user/driver/<username>", methods=["GET", "POST"])
def add_user_driver(username):
    if session.get('user_type') != 'admin': return redirect(url_for('login'))

    temp_data = session.get('temp_user_data')
    if not temp_data or temp_data.get('username') != username:
        flash("Session expired. Please start over.", "error")
        return redirect(url_for('add_user'))

    if request.method == "POST":
        temp_data['address'] = request.form.get("address")
        temp_data['license_number'] = request.form.get("license_number")
        temp_data['setup_complete'] = True

        col_drivers.insert_one(temp_data)
        session.pop('temp_user_data', None)

        flash(f"Driver {username} added successfully!", "success")
        return redirect(url_for('admin_dashboard'))
    return render_template_string(get_template("ADD_USER_STEP_3_HTML"), username=username, logo_url=LOGO_URL)


@app.route("/add_user/transporter/<username>", methods=["GET", "POST"])
def add_user_transporter(username):
    if session.get('user_type') != 'admin': return redirect(url_for('login'))

    temp_data = session.get('temp_user_data')
    if not temp_data or temp_data.get('username') != username:
        flash("Session expired.", "error")
        return redirect(url_for('add_user'))

    if request.method == "POST":
        temp_data['address'] = request.form.get("address")
        temp_data['gst'] = request.form.get("gst")
        # --- NEW CODE START ---
        # Capture the START UID and save to database
        temp_data['start_uid'] = request.form.get("start_uid")
        # --- NEW CODE END ---
        temp_data['setup_complete'] = True

        col_transporters.insert_one(temp_data)
        session.pop('temp_user_data', None)

        flash(f"Transporter {username} added successfully!", "success")
        return redirect(url_for('admin_dashboard'))
    return render_template_string(get_template("ADD_USER_STEP_4_HTML"), username=username, logo_url=LOGO_URL)


@app.route("/add_user/fps/<username>", methods=["GET", "POST"])
def add_user_fps(username):
    if session.get('user_type') != 'admin': return redirect(url_for('login'))

    temp_data = session.get('temp_user_data')
    if not temp_data or temp_data.get('username') != username:
        flash("Session expired.", "error")
        return redirect(url_for('add_user'))

    if request.method == "POST":
        temp_data['shop_name'] = request.form.get("shop_name")
        temp_data['address'] = request.form.get("address")
        temp_data['fps_no'] = request.form.get("fps_no")
        temp_data['setup_complete'] = True

        col_shopkeepers.insert_one(temp_data)
        session.pop('temp_user_data', None)

        flash(f"Shop Keeper {username} added successfully!", "success")
        return redirect(url_for('admin_dashboard'))
    return render_template_string(get_template("ADD_USER_STEP_5_HTML"), username=username, logo_url=LOGO_URL)


@app.route("/add_user/godown/<username>", methods=["GET", "POST"])
def add_user_godown(username):
    if session.get('user_type') != 'admin': return redirect(url_for('login'))

    temp_data = session.get('temp_user_data')
    if not temp_data or temp_data.get('username') != username:
        flash("Session expired.", "error")
        return redirect(url_for('add_user'))

    if request.method == "POST":
        temp_data['godown_name'] = request.form.get("name")
        temp_data['address'] = request.form.get("address")
        temp_data['setup_complete'] = True

        col_godown.insert_one(temp_data)
        session.pop('temp_user_data', None)

        flash(f"Godown Manager {username} added successfully!", "success")
        return redirect(url_for('admin_dashboard'))
    return render_template_string(get_template("ADD_USER_STEP_6_HTML"), username=username, logo_url=LOGO_URL)


# --- CAMERA SYSTEM ROUTES ---
@app.route("/device_info/<device_id>")
def device_info(device_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    # Check if user has permission to view this device
    username = session['username']
    role = session.get('role')
    user_doc, _ = find_user_in_db(username)

    if not user_doc and role != 'Administrator':
        session.clear()
        return redirect(url_for('login'))

    # Get vehicle info once to avoid redundant DB calls
    # Try both direct and case-insensitive lookup for robustness
    vehicle_info = col_vehicles.find_one({"device_id": device_id})
    if not vehicle_info:
        # Fallback to case-insensitive match if direct fails
        vehicle_info = col_vehicles.find_one({"device_id": {"$regex": f"^{re.escape(device_id)}$", "$options": "i"}})

    # Admin can view all devices
    if role != 'Administrator':
        if not vehicle_info:
            flash("Device not found or you don't have permission to view it.", "error")
            return redirect(url_for('user_dashboard'))

        if role == 'Transporter' and vehicle_info.get("transporter_name") != user_doc.get("name"):
            flash("You don't have permission to view this device.", "error")
            return redirect(url_for('user_dashboard'))

        if role == 'Driver' and vehicle_info.get("driver_name") != user_doc.get("name"):
            flash("You don't have permission to view this device.", "error")
            return redirect(url_for('user_dashboard'))

    try:
        r = requests.get(f"{FIREBASE_URL}/data/{device_id}.json?auth={FIREBASE_KEY}")
        device_data = r.json() or {}
        if not device_data:
            # If not in Firebase directly, try searching keys case-insensitively
            all_r = requests.get(f"{FIREBASE_URL}/data.json?auth={FIREBASE_KEY}")
            all_data = all_r.json() or {}
            # Find the true key that matches our device_id case-insensitively
            match_key = next((k for k in all_data if k.lower() == device_id.lower()), None)
            if match_key:
                device_data = all_data[match_key]
            else:
                return "Device not found", 404
        
        device_data = sanitize_data(device_data)
    except:
        return "Firebase Connection Error", 500

    def extract_stream_name(url, default_num):
        if not url: return f"Camera {default_num}"
        try:
            parts = url.strip().split('/')
            return parts[-1] if parts else f"Camera {default_num}"
        except:
            return f"Camera {default_num}"

    # Determine RTMP source preference
    source_pref = vehicle_info.get("rtmp_source", get_rtmp_source()) if vehicle_info else get_rtmp_source()
    
    if source_pref == 'mongo':
        mongo_rtmp = vehicle_info.get("mongo_rtmp", {}) if vehicle_info else {}
        rtmp_streams = {
            "1": {"name": extract_stream_name(mongo_rtmp.get("rtmp1"), 1), "url": mongo_rtmp.get("rtmp1", "")},
            "2": {"name": extract_stream_name(mongo_rtmp.get("rtmp2"), 2), "url": mongo_rtmp.get("rtmp2", "")},
            "3": {"name": extract_stream_name(mongo_rtmp.get("rtmp3"), 3), "url": mongo_rtmp.get("rtmp3", "")},
            "4": {"name": extract_stream_name(mongo_rtmp.get("rtmp4"), 4), "url": mongo_rtmp.get("rtmp4", "")}
        }
    else:
        rtmp_streams = {
            "1": {"name": extract_stream_name(device_data.get("rtmp1"), 1), "url": device_data.get("rtmp1", "")},
            "2": {"name": extract_stream_name(device_data.get("rtmp2"), 2), "url": device_data.get("rtmp2", "")},
            "3": {"name": extract_stream_name(device_data.get("rtmp3"), 3), "url": device_data.get("rtmp3", "")},
            "4": {"name": extract_stream_name(device_data.get("rtmp4"), 4), "url": device_data.get("rtmp4", "")}
        }

    # --- FETCH ASSIGNED USER CONTACT DETAILS ---
    gd_phone = "N/A"
    tr_phone = "N/A"
    dr_phone = "N/A"

    if vehicle_info:
        # 1. Godown Manager
        gd_name = vehicle_info.get("godown_manager")
        if gd_name:
            gd_usr = col_godown.find_one({"name": gd_name})
            if gd_usr: gd_phone = gd_usr.get("mobile", "N/A")

        # 2. Transporter
        tr_name = vehicle_info.get("transporter_name")
        if tr_name:
            tr_usr = col_transporters.find_one({"name": tr_name})
            if tr_usr: tr_phone = tr_usr.get("mobile", "N/A")

        # 3. Driver
        dr_name = vehicle_info.get("driver_name")
        if dr_name:
            dr_usr = col_drivers.find_one({"name": dr_name})
            if dr_usr: dr_phone = dr_usr.get("mobile", "N/A")

    return render_template_string(
        get_template("DEVICE_DASHBOARD_HTML"),
        device_id=device_id,
        initial_data=device_data,
        streams=rtmp_streams,
        logo_url=LOGO_URL,
        gd_phone=gd_phone,
        tr_phone=tr_phone,
        dr_phone=dr_phone
    )


# --- GPS API Routes ---
@app.route('/api/vehicle/<vehicle_id>')
def api_vehicle(vehicle_id):
    try:
        r = requests.get(f"{FIREBASE_URL}/data/{vehicle_id}.json?auth={FIREBASE_KEY}")
        if r.status_code == 200:
            data = r.json()
            if data:
                clean_data = sanitize_data(data)
                return jsonify(clean_data)
    except Exception as e:
        return jsonify({'error': str(e)})
    return jsonify({'error': 'Not found'})


@app.route('/api/save_config', methods=['POST'])
def save_config():
    try:
        req_data = request.json
        v_id = req_data.get('vehicle_id')
        config = req_data.get('config')
        url = f"{FIREBASE_URL}/data/{v_id}/record_config.json?auth={FIREBASE_KEY}"
        requests.patch(url, json=config)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)})


# --- Stream API Routes ---
@app.route("/play_rtmp")
def play_rtmp():
    src = request.args.get("src", "").strip()
    if not src: return jsonify({"error": "No URL"}), 400

    stream_id = str(abs(hash(src)))[:10]
    out_dir = STREAM_ROOT / stream_id
    out_dir.mkdir(exist_ok=True)

    with PROCESS_LOCK:
        LAST_HEARTBEAT[stream_id] = time.time()
        if stream_id not in PROCESS_TABLE or PROCESS_TABLE[stream_id].poll() is not None:
            PROCESS_TABLE[stream_id] = start_ffmpeg(src, out_dir)

    for _ in range(60):
        if (out_dir / "index.m3u8").exists(): break
        time.sleep(0.5)

    if not (out_dir / "index.m3u8").exists():
        return jsonify({"error": "Stream timeout"}), 500

    return jsonify({"hls_url": f"/hls/{stream_id}/index.m3u8", "stream_id": stream_id})


@app.route("/stop_stream", methods=["POST"])
def stop_stream_route():
    data = request.json
    sid = data.get("stream_id")
    if sid: kill_stream(sid)
    return jsonify({"status": "killed"})


@app.route("/keep_alive", methods=["POST"])
def keep_alive():
    data = request.json
    active_ids = data.get("stream_ids", [])
    now = time.time()
    with PROCESS_LOCK:
        for sid in active_ids:
            if sid in PROCESS_TABLE: LAST_HEARTBEAT[sid] = now
    return jsonify({"status": "ok"})


@app.route("/hls/<stream_id>/<path:filename>")
def hls(stream_id, filename):
    folder = STREAM_ROOT / stream_id
    if not folder.exists(): abort(404)
    return send_from_directory(folder, filename)


@app.route("/get_gps_update/<device_id>")
def get_gps_update(device_id):
    return api_vehicle(device_id)


@app.route("/toggle_camera", methods=["POST"])
def toggle_camera():
    if session.get('user_type') != 'admin':
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    
    try:
        data = request.json
        device_id = data.get("device_id")
        action = data.get("action")  # 'on' or 'off'
        
        if action in ['on', '1']:
            new_val = 1
        elif action in ['off', '0']:
            new_val = 0
        else:
            return jsonify({"success": False, "message": "Invalid action"}), 400
        
        # Update Firebase
        url = f"{FIREBASE_URL}/data/{device_id}.json?auth={FIREBASE_KEY}"
        response = requests.patch(url, json={"mosfet": new_val})
        
        if response.status_code == 200:
            return jsonify({"success": True, "message": f"Camera turned {action} successfuly"})
        else:
            return jsonify({"success": False, "message": "Failed to update Firebase"}), 500
            
    except Exception as e:
        print(f"Error toggling camera: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for('login'))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7777, debug=True, threaded=True)