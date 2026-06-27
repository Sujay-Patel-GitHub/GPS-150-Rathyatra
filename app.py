from flask import Flask, request, render_template, render_template_string, redirect, url_for, abort, jsonify, \
    session, flash, send_from_directory, Response, send_file, make_response
import requests
import os
import time
import re
import subprocess
import threading
import paramiko
import json
import mimetypes

# Force correct MIME types for JavaScript and CSS files (crucial for strict browser checks on Windows servers)
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
import zipfile
import io
from pathlib import Path
from datetime import datetime, timedelta, timezone

# --- IMPORT DATABASE & FIREBASE CONFIG ---
from mongodb import col_godown, col_transporters, col_drivers, col_shopkeepers, col_vehicles, col_settings, col_gps_recordings, col_map_recordings, col_sos_logs, col_gps_live, col_test
from firebase import LOGO_URL

app = Flask(__name__)
app.secret_key = "gps_rath_yatra_2026_secret_k3y_x9z"

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

@app.context_processor
def inject_sidebar_sections():
    try:
        config_doc = col_settings.find_one({"_id": "sidebar_sections"})
        if config_doc and "sections" in config_doc:
            sections = config_doc["sections"]
            updated = False
            # Check if tracking section exists, and update its href to force cache bust
            for s in sections:
                if s.get("id") == "tracking":
                    if s.get("href") != "/tracking?v=5":
                        s["href"] = "/tracking?v=5"
                        updated = True
                    break
            else:
                idx = -1
                for i, s in enumerate(sections):
                    if s.get("id") == "gps_monitoring":
                        idx = i
                        break
                if idx != -1:
                    sections.insert(idx + 1, {"id": "tracking", "name": "Tracking", "href": "/tracking?v=5", "icon": "fas fa-route", "visible": True})
                else:
                    sections.append({"id": "tracking", "name": "Tracking", "href": "/tracking?v=5", "icon": "fas fa-route", "visible": True})
                updated = True
            
            if updated:
                col_settings.update_one({"_id": "sidebar_sections"}, {"$set": {"sections": sections}}, upsert=True)
        else:
            sections = [
                {"id": "add_user", "name": "Add New User", "href": "/add_user", "icon": "fas fa-user-plus", "visible": True},
                {"id": "show_devices", "name": "Show Devices", "href": "/admin_dashboard", "icon": "fas fa-list-ul", "visible": True},
                {"id": "gps_monitoring", "name": "GPS Monitoring", "href": "/gps_monitoring", "icon": "fas fa-map-marked-alt", "visible": True},
                {"id": "tracking", "name": "Tracking", "href": "/tracking?v=5", "icon": "fas fa-route", "visible": True},
                {"id": "detailed_report", "name": "Detailed Report", "href": "/monthly_report", "icon": "fas fa-file-invoice", "visible": True},
                {"id": "list_roles", "name": "List Roles", "href": "/list_roles", "icon": "fas fa-users", "visible": True},
                {"id": "grouping", "name": "Grouping", "href": "/grouping", "icon": "fas fa-object-group", "visible": True},
                {"id": "manage_rtmp", "name": "RTMP Link Management", "href": "/manage_rtmp", "icon": "fas fa-link", "visible": True},
                {"id": "recordings", "name": "Recordings", "href": "/recordings", "icon": "fas fa-video", "visible": True},
                {"id": "map_recording", "name": "Map Recording", "href": "/map_recording", "icon": "fas fa-map-marked", "visible": True},
                {"id": "sos_logs", "name": "SOS Logs", "href": "/sos_logs", "icon": "fas fa-triangle-exclamation", "visible": True}
            ]
            col_settings.update_one({"_id": "sidebar_sections"}, {"$set": {"sections": sections}}, upsert=True)
    except Exception as e:
        print(f"Error loading sidebar sections: {e}")
        sections = [
            {"id": "add_user", "name": "Add New User", "href": "/add_user", "icon": "fas fa-user-plus", "visible": True},
            {"id": "show_devices", "name": "Show Devices", "href": "/admin_dashboard", "icon": "fas fa-list-ul", "visible": True},
            {"id": "gps_monitoring", "name": "GPS Monitoring", "href": "/gps_monitoring", "icon": "fas fa-map-marked-alt", "visible": True},
            {"id": "tracking", "name": "Tracking", "href": "/tracking?v=5", "icon": "fas fa-route", "visible": True},
            {"id": "detailed_report", "name": "Detailed Report", "href": "/monthly_report", "icon": "fas fa-file-invoice", "visible": True},
            {"id": "list_roles", "name": "List Roles", "href": "/list_roles", "icon": "fas fa-users", "visible": True},
            {"id": "grouping", "name": "Grouping", "href": "/grouping", "icon": "fas fa-object-group", "visible": True},
            {"id": "manage_rtmp", "name": "RTMP Link Management", "href": "/manage_rtmp", "icon": "fas fa-link", "visible": True},
            {"id": "recordings", "name": "Recordings", "href": "/recordings", "icon": "fas fa-video", "visible": True},
            {"id": "map_recording", "name": "Map Recording", "href": "/map_recording", "icon": "fas fa-map-marked", "visible": True},
            {"id": "sos_logs", "name": "SOS Logs", "href": "/sos_logs", "icon": "fas fa-triangle-exclamation", "visible": True}
        ]
    return {
        'sidebar_sections': sections
    }

# --- RTMP Stream Configuration ---
STREAM_ROOT = Path(__file__).parent / "streams"
STREAM_ROOT.mkdir(exist_ok=True)

PROCESS_TABLE = {}
LAST_HEARTBEAT = {}
PROCESS_LOCK = threading.Lock()
ZIP_PROGRESS = {} # job_id -> {current, total, status, file_path, timestamp}
ZIP_TEMP_DIR = Path(__file__).parent / "temp_zips"
ZIP_TEMP_DIR.mkdir(exist_ok=True)

def zip_cleanup_task():
    """Background task to delete old ZIP files every hour"""
    while True:
        try:
            now = time.time()
            # Clean directory
            for f in ZIP_TEMP_DIR.glob("*.zip"):
                if now - f.stat().st_mtime > 7200: # 2 hours old
                    f.unlink()
            
            # Clean dictionary
            to_delete = [jid for jid, info in ZIP_PROGRESS.items() 
                        if info.get('timestamp', 0) < now - 7200]
            for jid in to_delete:
                del ZIP_PROGRESS[jid]
                
        except Exception as e:
            print(f"Cleanup error: {e}")
        time.sleep(3600) # Run every hour

# Start cleanup thread
threading.Thread(target=zip_cleanup_task, daemon=True).start()


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
        return data.get("source", "mongo")
    return "mongo"


def get_live_gps(device_id):
    """Return latest GPS doc for a device from col_gps_live, or {}."""
    try:
        doc = col_gps_live.find_one({"device_id": device_id})
        if doc:
            doc.pop("_id", None)
            return doc
        return {}
    except:
        return {}


@app.route("/api/test_gps", methods=["POST"])
def test_gps():
    data = request.get_json(silent=True) or request.form.to_dict()
    try:
        col_test.insert_one({
            "lat": float(data.get("lat", 0)),
            "lng": float(data.get("lng", 0)),
            "timestamp": datetime.now()
        })
        return jsonify({"ok": True}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/truck_gps/<truck_id>", methods=["POST"])
def truck_gps(truck_id):
    data = request.get_json(silent=True) or request.form.to_dict()
    def f(key, default=0.0):
        try: return float(data.get(key, default))
        except: return default
    try:
        from mongodb import mongo_client
        db = mongo_client["gps_server_db"]
        
        lat_val = f("lat")
        lng_val = f("lng")
        speed_val = f("speed")
        motion_val = str(data.get("motion", "unknown"))
        now = datetime.now()
        
        # 1. Insert into new_devices
        db["new_devices"].insert_one({
            "truck_id": truck_id,
            "lat":      lat_val,
            "lng":      lng_val,
            "speed":    speed_val,
            "motion":   motion_val,
            "timestamp": now
        })
        
        # 2. Update gps_live so the rest of the app gets this live coordinate instantly!
        db["gps_live"].update_one(
            {"device_id": truck_id},
            {"$set": {
                "device_id": truck_id,
                "lat":      lat_val,
                "lng":      lng_val,
                "speed":    speed_val,
                "date":     now.strftime("%d-%m-%Y"),
                "time":     now.strftime("%H:%M:%S"),
                "updated_at": now
            }},
            upsert=True
        )
        
        # 3. Save to map_recordings by default!
        try:
            db["map_recordings"].insert_one({
                "device_id": truck_id,
                "lat":      lat_val,
                "lng":      lng_val,
                "speed":    speed_val,
                "timestamp": now,
                "created_at": now
            })
        except Exception as ie:
            pass
            
        return jsonify({"ok": True}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── MongoDB Viewer ──────────────────────────────────────────
@app.route("/mongo_viewer")
def mongo_viewer():
    return render_template("mongo_viewer.html")


@app.route("/api/mongo_collections")
def mongo_collections():
    from mongodb import mongo_client
    db = mongo_client["gps_server_db"]
    result = []
    for name in sorted(db.list_collection_names()):
        count = db[name].count_documents({})
        latest = db[name].find_one(sort=[("timestamp", -1)]) or db[name].find_one()
        ts = latest.get("timestamp", "") if latest else ""
        result.append({
            "name": name,
            "count": count,
            "latest_ts": ts.strftime("%d-%b-%Y %I:%M:%S %p") if hasattr(ts, "strftime") else str(ts)
        })
    return jsonify(result)


@app.route("/api/mongo_data/<collection>")
def mongo_data(collection):
    from mongodb import mongo_client
    db = mongo_client["gps_server_db"]
    docs = list(db[collection].find({}, {"_id": 0}).sort("timestamp", -1))
    for d in docs:
        if "timestamp" in d and hasattr(d["timestamp"], "strftime"):
            d["timestamp"] = d["timestamp"].strftime("%d-%b-%Y %I:%M:%S %p")
    return jsonify(docs)


@app.route("/api/mongo_drop/<collection>", methods=["DELETE"])
def mongo_drop(collection):
    # Permanently disabled — no collection drops allowed
    return jsonify({"ok": False, "error": "Drop operations are disabled to protect data."}), 403


# ── Device Remote Reset ──────────────────────────────────────
@app.route("/api/reset_device/<truck_id>", methods=["POST"])
def reset_device(truck_id):
    """Admin pushes new config. ESP will pick it up on next poll."""
    data = request.get_json(silent=True) or request.form.to_dict()
    try:
        from mongodb import mongo_client
        col = mongo_client["gps_server_db"]["device_resets"]
        col.update_one(
            {"truck_id": truck_id},
            {"$set": {
                "truck_id":  truck_id,
                "new_name":  data.get("new_name", truck_id),
                "new_ssid":  data.get("new_ssid", ""),
                "new_pass":  data.get("new_pass", ""),
                "status":    "pending",
                "queued_at": datetime.now(),
                "done_at":   None
            }},
            upsert=True
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/get_pending_reset/<truck_id>", methods=["GET"])
def get_pending_reset(truck_id):
    """ESP polls this. Returns pending config if one is queued."""
    try:
        from mongodb import mongo_client
        col = mongo_client["gps_server_db"]["device_resets"]
        doc = col.find_one({"truck_id": truck_id, "status": "pending"})
        if doc:
            return jsonify({
                "pending":  True,
                "new_name": doc.get("new_name", truck_id),
                "new_ssid": doc.get("new_ssid", ""),
                "new_pass": doc.get("new_pass", "")
            })
        return jsonify({"pending": False})
    except Exception as e:
        return jsonify({"pending": False, "error": str(e)})


@app.route("/api/reset_confirmed/<truck_id>", methods=["POST"])
def reset_confirmed(truck_id):
    """ESP calls this after writing new EEPROM values."""
    try:
        from mongodb import mongo_client
        col = mongo_client["gps_server_db"]["device_resets"]
        col.update_one(
            {"truck_id": truck_id},
            {"$set": {"status": "done", "done_at": datetime.now()}}
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/reset_status/<truck_id>", methods=["GET"])
def reset_status(truck_id):
    """UI polls this to check if ESP has confirmed the reset."""
    try:
        from mongodb import mongo_client
        col = mongo_client["gps_server_db"]["device_resets"]
        doc = col.find_one({"truck_id": truck_id})
        if not doc:
            return jsonify({"status": "none"})
        done_at = doc.get("done_at")
        return jsonify({
            "status":    doc.get("status", "none"),
            "done_at":   done_at.strftime("%d-%b-%Y %I:%M:%S %p") if done_at else None,
            "new_name":  doc.get("new_name"),
            "queued_at": doc["queued_at"].strftime("%d-%b-%Y %I:%M:%S %p") if doc.get("queued_at") else None
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)})


# ── MOSFET Control ───────────────────────────────────────────
@app.route("/api/truck_last_seen/<truck_id>")
def truck_last_seen(truck_id):
    try:
        from mongodb import mongo_client
        col = mongo_client["gps_server_db"]["new_devices"]
        doc = col.find_one({"truck_id": truck_id}, sort=[("timestamp", -1)])
        if doc and doc.get("timestamp"):
            ts = doc["timestamp"]
            threshold_sec = get_power_off_threshold() * 60
            is_off = (datetime.now() - ts).total_seconds() > threshold_sec
            return jsonify({
                "ok": True,
                "date": ts.strftime("%d-%b-%Y"),
                "time": ts.strftime("%I:%M:%S %p"),
                "power_off": is_off
            })
        return jsonify({"ok": False})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/assign_device", methods=["POST"])
def assign_device():
    data     = request.get_json(silent=True) or {}
    truck_id = str(data.get("truck_id", "")).strip()
    username = str(data.get("username", "")).strip()
    role     = str(data.get("role", "")).strip()
    if not truck_id or not username or not role:
        return jsonify({"ok": False, "error": "truck_id, username and role are required"}), 400
    doc = {
        "truck_id":      truck_id,
        "username":      username,
        "role":          role,
        "assigned_at":   datetime.now(),
    }
    # Role-specific detail fields (AKHADA for now; extendable for RATH etc.)
    detail_keys = ["officer_pi", "pi_contact", "police_station", "vehicle_plate",
                   "driver_name", "driver_mobile", "front_rtmp", "rear_rtmp",
                   "contractor_name", "contractor_mobile", "password"]
    for k in detail_keys:
        val = str(data.get(k, "")).strip()
        if val:
            doc[k] = val
    try:
        from mongodb import mongo_client
        gps_db = mongo_client["gps_server_db"]
        gps_db["assign_devices"].update_one(
            {"truck_id": truck_id},
            {"$set": doc},
            upsert=True
        )
        # Mark truck as registered so it moves to Registered tab
        gps_db["registered_trucks"].update_one(
            {"truck_id": truck_id},
            {"$set": {"truck_id": truck_id, "registered_at": datetime.now()}},
            upsert=True
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/assign_device/<truck_id>", methods=["GET"])
def get_assign_device(truck_id):
    try:
        from mongodb import mongo_client
        doc = mongo_client["gps_server_db"]["assign_devices"].find_one(
            {"truck_id": truck_id}, {"_id": 0, "password": 0}
        )
        if not doc:
            return jsonify({"ok": False, "error": "Not found"}), 404
        if "assigned_at" in doc:
            doc["assigned_at"] = str(doc["assigned_at"])
        return jsonify({"ok": True, "doc": doc})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/mosfet_set/<truck_id>", methods=["POST"])
def mosfet_set(truck_id):
    data = request.get_json(silent=True) or {}
    state = int(data.get("state", 0))  # 1 = ON, 0 = OFF
    try:
        from mongodb import mongo_client
        mongo_client["gps_server_db"]["mosfet_states"].update_one(
            {"truck_id": truck_id},
            {"$set": {"truck_id": truck_id, "state": state, "updated_at": datetime.now()}},
            upsert=True
        )
        # Keep gps_live in sync
        col_gps_live.update_one(
            {"device_id": truck_id},
            {"$set": {"mosfet": state}},
            upsert=True
        )
        return jsonify({"ok": True, "state": state})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/mosfet_state/<truck_id>", methods=["GET"])
def mosfet_state(truck_id):
    try:
        from mongodb import mongo_client
        doc = mongo_client["gps_server_db"]["mosfet_states"].find_one({"truck_id": truck_id})
        return jsonify({"state": doc["state"] if doc else 0})
    except Exception as e:
        return jsonify({"state": 0, "error": str(e)})


# ── Device Registration ──────────────────────────────────────
@app.route("/api/devices/all", methods=["GET"])
def devices_all():
    """Returns all truck IDs seen in new_devices, and which are registered."""
    try:
        from mongodb import mongo_client
        db = mongo_client["gps_server_db"]
        # All unique truck IDs that have ever pushed data
        seen = db["new_devices"].distinct("truck_id")
        # Registered truck IDs
        registered = [d["truck_id"] for d in db["registered_trucks"].find({}, {"truck_id": 1})]
        reg_set = set(registered)
        return jsonify({
            "registered":   [t for t in seen if t in reg_set],
            "unregistered": [t for t in seen if t not in reg_set]
        })
    except Exception as e:
        return jsonify({"registered": [], "unregistered": [], "error": str(e)})


@app.route("/api/devices/register/<truck_id>", methods=["POST"])
def register_device(truck_id):
    try:
        from mongodb import mongo_client
        col = mongo_client["gps_server_db"]["registered_trucks"]
        col.update_one({"truck_id": truck_id}, {"$set": {"truck_id": truck_id, "registered_at": datetime.now()}}, upsert=True)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/devices/unregister/<truck_id>", methods=["DELETE"])
def unregister_device(truck_id):
    try:
        from mongodb import db
        db["registered_trucks"].delete_many({"truck_id": truck_id})
        db["registered_vehicles"].delete_many({"device_id": truck_id})
        db["gps_live"].delete_many({"device_id": truck_id})
        db["map_recordings"].delete_many({"device_id": truck_id})
        db["sos_logs"].delete_many({"device_id": truck_id})
        db["new_devices"].delete_many({"truck_id": truck_id})
        db["new_devices"].delete_many({"device_id": truck_id})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def get_power_off_threshold():
    return 60  # fixed default, time_threshold page removed


# --- CAMERA SYSTEM HELPER FUNCTIONS ---
def safe_float(val):
    if val is None: return 0.0
    if isinstance(val, (float, int)): return float(val)
    try:
        clean_val = str(val).replace('"', '').replace("'", '').strip()
        return float(clean_val)
    except:
        return 0.0


def is_valid_gps_coordinate(lat, lng):
    """
    Checks if the GPS coordinate is valid and within the bounds of India.
    India's approximate bounding box:
    Latitude: 8.0 to 38.0
    Longitude: 68.0 to 98.0
    """
    if lat is None or lng is None:
        return False
    try:
        lat_val = float(lat)
        lng_val = float(lng)
        # Skip exact zeros or coordinates very close to 0
        if abs(lat_val) < 0.0001 or abs(lng_val) < 0.0001:
            return False
        # Bounding box check for India
        if not (8.0 <= lat_val <= 38.0 and 68.0 <= lng_val <= 98.0):
            return False
        return True
    except (ValueError, TypeError):
        return False


def filter_coordinate_spikes(points):
    """
    Filters out transient coordinate spikes (single bad GPS pings) from a list of points.
    A spike is detected if a point deviates significantly from both the previous and next points,
    but the previous and next points are close to each other.
    Also checks for impossible speed jumps between consecutive points.
    """
    if len(points) < 3:
        return points
        
    import math
    from datetime import datetime
    
    def haversine_distance(coord1, coord2):
        R = 6371.0 # km
        lat1, lon1 = math.radians(coord1[0]), math.radians(coord1[1])
        lat2, lon2 = math.radians(coord2[0]), math.radians(coord2[1])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    def get_timestamp(pt):
        # Extract timestamp as a datetime or float timestamp
        ts = pt.get('ts')
        if ts is not None:
            return float(ts)
        t_obj = pt.get('timestamp')
        if isinstance(t_obj, datetime):
            return t_obj.timestamp()
        if isinstance(t_obj, str):
            try:
                # parse ISO string or similar
                return datetime.fromisoformat(t_obj.replace('Z', '+00:00')).timestamp()
            except:
                pass
        return None

    n = len(points)
    to_remove = set()
    
    for i in range(1, n - 1):
        prev_pt = points[i-1]
        curr_pt = points[i]
        next_pt = points[i+1]
        
        try:
            d1 = haversine_distance((prev_pt['lat'], prev_pt['lng']), (curr_pt['lat'], curr_pt['lng']))
            d2 = haversine_distance((curr_pt['lat'], curr_pt['lng']), (next_pt['lat'], next_pt['lng']))
            d3 = haversine_distance((prev_pt['lat'], prev_pt['lng']), (next_pt['lat'], next_pt['lng']))
            
            t_prev = get_timestamp(prev_pt)
            t_curr = get_timestamp(curr_pt)
            t_next = get_timestamp(next_pt)
            
            # Check time gaps (in seconds)
            dt1 = (t_curr - t_prev) if (t_prev is not None and t_curr is not None) else None
            dt2 = (t_next - t_curr) if (t_curr is not None and t_next is not None) else None
            
            # Double-sided jump spike conditions:
            # 1. Massive jump (e.g. > 15km) in both directions, and returning close to prev (shortcut d3 is small)
            if d1 > 15.0 and d2 > 15.0 and d3 < 10.0:
                if dt1 is None or dt2 is None or (dt1 < 600 and dt2 < 600):
                    to_remove.add(i)
                    continue
                    
            # 2. Impossible speeds (e.g. > 180 km/h) on both legs, returning to a small fraction of the total jump distance
            if dt1 and dt2 and dt1 > 0 and dt2 > 0:
                speed1 = (d1 / dt1) * 3600.0
                speed2 = (d2 / dt2) * 3600.0
                if speed1 > 180.0 and speed2 > 180.0 and d3 < (d1 + d2) * 0.2:
                    to_remove.add(i)
                    continue
                    
            # 3. Fallback absolute distance jump without time check (e.g., > 50km jump and return, shortcut is small)
            if d1 > 50.0 and d2 > 50.0 and d3 < 10.0:
                to_remove.add(i)
                continue
                
        except Exception as e:
            pass
            
    # Handle first point spike check
    if n >= 2:
        try:
            d = haversine_distance((points[0]['lat'], points[0]['lng']), (points[1]['lat'], points[1]['lng']))
            t0 = get_timestamp(points[0])
            t1 = get_timestamp(points[1])
            dt = (t1 - t0) if (t1 is not None and t0 is not None) else None
            
            if d > 50.0:
                if dt is None or dt < 600:
                    to_remove.add(0)
        except:
            pass
            
    # Handle last point spike check
    if n >= 2:
        try:
            d = haversine_distance((points[n-2]['lat'], points[n-2]['lng']), (points[n-1]['lat'], points[n-1]['lng']))
            t_pen = get_timestamp(points[n-2])
            t_last = get_timestamp(points[n-1])
            dt = (t_last - t_pen) if (t_last is not None and t_pen is not None) else None
            
            if d > 50.0:
                if dt is None or dt < 600:
                    to_remove.add(n-1)
        except:
            pass
            
    return [points[i] for i in range(n) if i not in to_remove]


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
    log_path = out_dir / "ffmpeg.log"
    print("▶️ Starting FFmpeg:", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=open(log_path, "w"))
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
    elif session.get('user_type') == 'truck':
        return redirect('/device_info/' + session.get('truck_id', ''))
    elif session.get('user_type') == 'akhada':
        return redirect('/akhada_dashboard')
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

        if username == "superadmin" and password == "superadmin":
            session['username'] = 'superadmin'
            session['user_type'] = 'admin'
            session['role'] = 'Superadmin'
            return redirect(url_for('admin_dashboard'))

        user_doc, role_name = find_user_in_db(username)
        if user_doc and user_doc.get("password") == password:
            session['username'] = username
            session['user_type'] = 'user'
            session['role'] = role_name
            return redirect(url_for('user_dashboard'))

        # Check akhada / truck users
        try:
            from mongodb import mongo_client
            for doc in mongo_client["gps_server_db"]["assign_devices"].find({"role": {"$in": ["AKHADA_USER", "TRUCK_USER"]}}):
                su = str(doc.get("username", "")).strip()
                sp = str(doc.get("password", "")).strip()
                if su.lower() == username.strip().lower() and sp == password.strip():
                    session.clear()
                    role = doc.get("role")
                    if role == "TRUCK_USER":
                        session['user_type'] = 'truck'
                        session['truck_username'] = su
                        session['truck_id'] = str(doc.get("truck_id", ""))
                        return redirect('/device_info/' + doc.get("truck_id", ""))
                    else:
                        session['user_type'] = 'akhada'
                        session['akhada_username'] = su
                        session['akhada_truck_id'] = str(doc.get("truck_id", ""))
                        return redirect('/akhada_dashboard')
        except Exception:
            pass

        flash("Invalid username or password.", "error")
        return render_template_string(get_template("LOGIN_HTML"), logo_url=LOGO_URL)

    return render_template_string(get_template("LOGIN_HTML"), logo_url=LOGO_URL)


@app.route("/api/push_gps/<device_id>", methods=["POST"])
def push_gps(device_id):
    """Devices POST their latest GPS here instead of Firebase."""
    data = request.get_json(silent=True) or request.form.to_dict()
    if not data:
        return jsonify({"success": False, "error": "No data"}), 400
    
    lat = data.get("lat")
    lng = data.get("lng") or data.get("lon")
    speed = data.get("speed", 0)
    
    col_gps_live.update_one(
        {"device_id": device_id},
        {"$set": {
            "device_id": device_id,
            "lat": lat,
            "lng": lng,
            "speed": speed,
            "date": data.get("date", ""),
            "time": data.get("time", ""),
            "mosfet": data.get("mosfet"),
            "sos": data.get("sos", 0),
            "rfid_data": data.get("rfid_data", {}),
            "rtmp1": data.get("rtmp1", ""),
            "rtmp2": data.get("rtmp2", ""),
            "rtmp3": data.get("rtmp3", ""),
            "rtmp4": data.get("rtmp4", ""),
            "updated_at": datetime.now()
        }},
        upsert=True
    )
    
    # Save to map_recordings and new_devices by default!
    try:
        if lat is not None and lng is not None:
            lat_f = float(lat)
            lng_f = float(lng)
            speed_f = float(speed)
            if is_valid_gps_coordinate(lat_f, lng_f):
                col_map_recordings.insert_one({
                    "device_id": device_id,
                    "lat": lat_f,
                    "lng": lng_f,
                    "speed": speed_f,
                    "timestamp": datetime.now(),
                    "created_at": datetime.now()
                })
                
                # Also insert into new_devices to keep both collections completely synced
                try:
                    from mongodb import mongo_client
                    mongo_client["gps_server_db"]["new_devices"].insert_one({
                        "truck_id": device_id,
                        "lat": lat_f,
                        "lng": lng_f,
                        "speed": speed_f,
                        "motion": "unknown",
                        "timestamp": datetime.now()
                    })
                except Exception:
                    pass
    except Exception as e:
        pass
        
    return jsonify({"success": True})


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


    registered_map_local = {v.get("device_id"): v for v in col_vehicles.find() if v.get("device_id")}
    all_hardware_ids = list(registered_map_local.keys())
    raw_firebase_data = {}

    registered_map = registered_map_local

    devices_display_list = []
    for i, dev_id in enumerate(all_hardware_ids, start=1):
        # Get GPS data from MongoDB
        gps_lat = None
        gps_lng = None
        last_updated = "N/A"

        location = {}
        try:
            device_firebase_data = get_live_gps(dev_id) or {}
            if device_firebase_data:
                location = device_firebase_data
                if location:
                    # Get coordinates
                    try:
                        if "lat" in location:
                            gps_lat = float(location.get("lat", 0.0))
                        if "lng" in location or "lon" in location:
                            gps_lng = float(location.get("lng") or location.get("lon", 0.0))
                    except:
                        pass
                    
            # Get timestamp and convert to IST
            last_updated_date = "N/A"
            last_updated_time = ""
            is_power_off = True
            is_online_1m = False
            try:
                date_str = location.get("date", "")
                time_str = location.get("time", "")
                
                if date_str and time_str:
                    utc_time = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M:%S")
                    ist_time = utc_time
                    
                    # Apply Dynamic Offsets
                    ist_time = apply_time_offset(ist_time, dev_id, None)

                    # Check if more than configured threshold ago
                    threshold_sec = get_power_off_threshold() * 60
                    is_power_off = (datetime.now() - ist_time).total_seconds() > threshold_sec
                    is_online_1m = (datetime.now() - ist_time).total_seconds() <= 60

                    last_updated_date = ist_time.strftime("%d-%b-%Y")
                    last_updated_time = ist_time.strftime("%I:%M:%S %p")
                else:
                    is_power_off = True
                    is_online_1m = False
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

            # Get device raw data from MongoDB live GPS
            device_raw = get_live_gps(dev_id) or {}
            
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
                "is_online_1m": is_online_1m,
                "camera_status": camera_status,
                "is_recording": is_recording,
                "trip_number": latest_trip_num,
                "trip_status": "Ongoing" if raw_trip_status == "1" else "Stop"
            })
        else:
            # Get device raw data from MongoDB live GPS
            device_raw = get_live_gps(dev_id) or {}
            
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
                "is_online_1m": is_online_1m,
                "camera_status": camera_status,
                "is_recording": is_recording,
                "trip_number": latest_trip_num,
                "trip_status": "Ongoing" if raw_trip_status == "1" else "Stop"
            })

    # ── Append ESP trucks from new_devices + merge assign_devices info ──
    try:
        from mongodb import mongo_client
        gps_db = mongo_client["gps_server_db"]
        registered_truck_ids = set(d["truck_id"] for d in gps_db["registered_trucks"].find({}, {"truck_id": 1}))
        assign_map = {d["truck_id"]: d for d in gps_db["assign_devices"].find()}
        # Build latest-per-truck map from new_devices (case-insensitive key = upper)
        latest_map = {}
        for doc in gps_db["new_devices"].find({}, {"truck_id": 1, "lat": 1, "lng": 1, "timestamp": 1}).sort("timestamp", -1):
            tid = doc.get("truck_id")
            if tid and tid.upper() not in latest_map:
                latest_map[tid.upper()] = doc

        # Always override timestamp from new_devices for any device that has ESP data
        threshold_sec = get_power_off_threshold() * 60
        for dev in devices_display_list:
            ndoc = latest_map.get(dev["device_name"].upper())
            if ndoc:
                ts = ndoc.get("timestamp")
                if ts:
                    dev["last_updated_date"] = ts.strftime("%d-%b-%Y")
                    dev["last_updated_time"] = ts.strftime("%I:%M:%S %p")
                    dev["is_power_off"] = (datetime.now() - ts).total_seconds() > threshold_sec
                    dev["is_online_1m"] = (datetime.now() - ts).total_seconds() <= 60
                if ndoc.get("lat") is not None:
                    dev["gps_lat"] = ndoc.get("lat")
                    dev["gps_lng"] = ndoc.get("lng")
            # Merge assign_devices info into existing devices too
            tid = dev["device_name"]
            if tid in assign_map:
                a = assign_map[tid]
                dev.setdefault("assign_role",       a.get("role", ""))
                dev.setdefault("assign_username",   a.get("username", ""))
                dev.setdefault("assign_driver",     a.get("driver_name", ""))
                dev.setdefault("assign_driver_mob", a.get("driver_mobile", ""))
                dev.setdefault("assign_contractor", a.get("contractor_name", ""))
                dev.setdefault("assign_plate",      a.get("vehicle_plate", ""))
                dev.setdefault("assign_officer",    a.get("officer_pi", ""))
                dev.setdefault("assign_front_rtmp", a.get("front_rtmp", ""))

        existing_ids = set(d["device_name"] for d in devices_display_list)
        for tid, doc in latest_map.items():
            if tid in existing_ids:
                continue
            ts = doc.get("timestamp")
            ts_date = ts.strftime("%d-%b-%Y") if ts else "N/A"
            ts_time = ts.strftime("%I:%M:%S %p") if ts else ""
            is_po = (datetime.now() - ts).total_seconds() > threshold_sec if ts else True
            is_ol_1m = (datetime.now() - ts).total_seconds() <= 60 if ts else False
            a = assign_map.get(tid, {})
            devices_display_list.append({
                "id": len(devices_display_list) + 1,
                "device_name": tid,
                "is_registered": tid in registered_truck_ids,
                # assign_devices fields
                "assign_role":        a.get("role", ""),
                "assign_username":    a.get("username", ""),
                "assign_driver":      a.get("driver_name", ""),
                "assign_driver_mob":  a.get("driver_mobile", ""),
                "assign_contractor":  a.get("contractor_name", ""),
                "assign_plate":       a.get("vehicle_plate", ""),
                "assign_officer":     a.get("officer_pi", ""),
                "assign_front_rtmp":  a.get("front_rtmp", ""),
                # legacy fields (kept blank)
                "rc_number": "", "transporter": "", "transporter_phone": "",
                "godown_manager": "", "godown_phone": "", "driver_name": a.get("driver_name",""), "driver_phone": "",
                "gps_lat": doc.get("lat"), "gps_lng": doc.get("lng"),
                "last_updated_date": ts_date, "last_updated_time": ts_time,
                "is_power_off": is_po,
                "is_online_1m": is_ol_1m,
                "camera_status": "N/A",
                "is_recording": False, "trip_number": 0, "trip_status": "N/A"
            })
    except Exception as e:
        pass  # don't break dashboard if new_devices query fails

    return render_template_string(
        get_template("SHOW_DEVICES_HTML"),
        devices=devices_display_list,
        drivers=drivers_list,
        transporters=transporters_list,
        godown_managers=godown_managers_list,
        users_by_role=users_by_role,
        logo_url=LOGO_URL
    )


def get_processed_vehicles_list():
    # Get all devices from MongoDB
    vehicles_dict = {}
    for vehicle in col_vehicles.find({}):
        did = vehicle.get("device_id")
        if did:
            vehicles_dict[did.strip()] = vehicle
    all_device_ids = [did for did in vehicles_dict.keys() if did]

    # Combine MongoDB vehicles with live GPS data
    vehicles_list = []
    for device_id in all_device_ids:
        vehicle_data = vehicles_dict.get(device_id.strip(), {})

        # Get GPS coordinates from MongoDB live GPS
        lat = None
        lng = None
        has_gps = False
        speed = 0
        last_updated_date = "N/A"
        last_updated_time = ""
        is_power_off = True

        location = {}
        try:
            device_firebase_data = get_live_gps(device_id) or {}
            if device_firebase_data:
                location = device_firebase_data
                if location:
                    try:
                        if "lat" in location:
                            lat = float(location.get("lat", 0.0))
                            has_gps = True
                        if "lng" in location or "lon" in location:
                            lng = float(location.get("lng") or location.get("lon", 0.0))
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
                        ist_time = utc_time
                        
                        # Apply Time Offset
                        ist_time = apply_time_offset(ist_time, device_id, vehicle_data)

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
        if lat is None or lng is None:
            lat = 23.0225  # Default to Ahmedabad
            lng = 72.5714
            has_gps = False
        
        # Get camera status (mosfet)
        device_raw = get_live_gps(device_id) or {}
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
            "display_name": vehicle_data.get("display_name", device_id),
            "record_config": vehicle_data.get("record_config"),
            "calibrate_pending": vehicle_data.get("calibrate_pending"),
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
            "speed": speed,
            "bearing": float(location.get("bearing", 0.0)),
            "satellites": int(location.get("satellites", 12)),
            "hdop": float(location.get("hdop", 1.0))
        })

    # Also include ESP trucks from registered_trucks (new GPS db)
    try:
        from mongodb import mongo_client
        gps_db = mongo_client["gps_server_db"]
        existing_ids = set(v["device_id"] for v in vehicles_list)
        latest_map = {}
        for doc in gps_db["new_devices"].find({}, {"truck_id":1,"lat":1,"lng":1,"speed":1,"timestamp":1,"bearing":1,"satellites":1,"hdop":1}).sort("timestamp", -1):
            tid = doc.get("truck_id")
            if tid and tid.upper() not in latest_map:
                latest_map[tid.upper()] = doc
        assign_map = {d["truck_id"]: d for d in gps_db["assign_devices"].find({}, {"_id":0})}
        threshold_sec = get_power_off_threshold() * 60
        for doc in gps_db["registered_trucks"].find({}, {"truck_id":1}):
            tid = doc.get("truck_id")
            if not tid or tid in existing_ids:
                continue
            nd = latest_map.get(tid.upper(), {})
            ts = nd.get("timestamp")
            lat = nd.get("lat")
            lng = nd.get("lng") or nd.get("lon")
            if lat is not None and lng is not None:
                has_gps = True
            else:
                lat = 23.0225
                lng = 72.5714
                has_gps = False
            a = assign_map.get(tid, {})
            vehicle_data = vehicles_dict.get(tid.strip(), {})
            vehicles_list.append({
                "device_id": tid,
                "display_name": vehicle_data.get("display_name", tid),
                "record_config": vehicle_data.get("record_config"),
                "calibrate_pending": vehicle_data.get("calibrate_pending"),
                "rc_number": a.get("vehicle_plate", ""),
                "driver_name": a.get("driver_name", ""),
                "transporter_name": a.get("contractor_name", ""),
                "godown_manager": "",
                "lat": lat, "lng": lng, "has_gps": has_gps,
                "last_updated_date": ts.strftime("%d-%b-%Y") if ts else "N/A",
                "last_updated_time": ts.strftime("%I:%M:%S %p") if ts else "",
                "is_power_off": (datetime.now() - ts).total_seconds() > threshold_sec if ts else True,
                "camera_status": "No Data", "is_recording": False,
                "trip_number": 0, "trip_status": "Stop",
                "speed": nd.get("speed", 0),
                "bearing": float(nd.get("bearing", 0.0)),
                "satellites": int(nd.get("satellites", 12)),
                "hdop": float(nd.get("hdop", 1.0))
            })
    except Exception as e:
        print(f"Error adding ESP trucks to vehicles list: {e}")

    return vehicles_list


@app.route("/gps_monitoring")
def gps_monitoring():
    if session.get('user_type') != 'admin':
        return redirect(url_for('login'))

    vehicles_list = get_processed_vehicles_list()
    try:
        from mongodb import mongo_client
        assign_map = {}
        for d in mongo_client["gps_server_db"]["assign_devices"].find({}, {"_id": 0}):
            assign_map[d["truck_id"]] = d
    except Exception:
        assign_map = {}

    return render_template_string(
        get_template("GPS_MONITORING_HTML"),
        vehicles=vehicles_list,
        assign_map=assign_map
    )


@app.route("/get_vehicle_gps/<device_id>")
def get_vehicle_gps(device_id):
    if session.get('user_type') not in ('admin', 'truck', 'akhada', 'user'):
        return jsonify({"error": "Unauthorized"}), 403
    
    try:
        # Fetch device data from MongoDB
        device_data = get_live_gps(device_id) or {}

        # Flat structure — use device_data directly as location
        location = device_data

        # 1. Fetch metadata (camera, recording, trip status) first, so they are available for all returns
        if "mosfet" in device_data:
            mosfet_val = device_data.get("mosfet")
            camera_status = "On" if str(mosfet_val) == "1" else "Off"
        else:
            camera_status = "No Data"
        
        # Check if currently recording
        with RECORDING_LOCK:
            is_recording = device_id in RECORDING_SESSIONS

        # Get RFID / Trip Status
        rfid_data = device_data.get("rfid_data", {})
        raw_trip_status = str(rfid_data.get("status", "0"))
        trip_status = "Ongoing" if raw_trip_status == "1" else "Stop"
        
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

        # Try to get lat and lon
        lat = None
        lon = None

        if location:
            try:
                if "lat" in location:
                    lat = float(location.get("lat", 0.0))
                if "lng" in location or "lon" in location:
                    lon = float(location.get("lng") or location.get("lon", 0.0))
            except:
                pass

        # Get Speed
        speed = device_data.get("speed", "0")

        # Get date and time from location and convert to IST
        last_updated_date = "N/A"
        last_updated_time = ""
        is_power_off = True
        is_online_1m = False
        try:
            date_str = location.get("date", "")
            time_str = location.get("time", "")
            
            if date_str and time_str:
                # Parse the time (format: "HH:MM:SS" and date: "DD-MM-YYYY")
                utc_time = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M:%S")
                
                # Convert to IST (UTC + 5:30)
                ist_time = utc_time
                
                # Apply Dynamic Offsets
                ist_time = apply_time_offset(ist_time, device_id, None)

                # Check if more than configured threshold ago
                threshold_sec = get_power_off_threshold() * 60
                is_power_off = (datetime.now() - ist_time).total_seconds() > threshold_sec
                is_online_1m = (datetime.now() - ist_time).total_seconds() <= 60

                # Format the output
                last_updated_date = ist_time.strftime("%d-%b-%Y")
                last_updated_time = ist_time.strftime("%I:%M:%S %p")
            else:
                is_power_off = True
                is_online_1m = False
        except Exception as e:
            print(f"Error converting timestamp for {device_id}: {e}")
            pass
        
        # Fallback: pull timestamp (and GPS) from new_devices if Firebase gave nothing
        try:
            from mongodb import mongo_client
            nd = mongo_client["gps_server_db"]["new_devices"].find_one(
                {"truck_id": {"$regex": f"^{device_id}$", "$options": "i"}}, sort=[("timestamp", -1)]
            )
            if nd and nd.get("timestamp"):
                ts = nd["timestamp"]
                threshold_sec = get_power_off_threshold() * 60
                last_updated_date = ts.strftime("%d-%b-%Y")
                last_updated_time = ts.strftime("%I:%M:%S %p")
                is_power_off = (datetime.now() - ts).total_seconds() > threshold_sec
                is_online_1m = (datetime.now() - ts).total_seconds() <= 60
                if lat is None and nd.get("lat") is not None:
                    lat = nd["lat"]
                    lon = nd["lng"]
        except Exception:
            pass

        # Check if we have valid GPS data — fall back to last known from new_devices
        if lat is None or lon is None:
            try:
                from mongodb import mongo_client
                nd = mongo_client["gps_server_db"]["new_devices"].find_one(
                    {"truck_id": {"$regex": f"^{device_id}$", "$options": "i"}, "lat": {"$exists": True, "$ne": 0}},
                    sort=[("timestamp", -1)]
                )
                if nd and nd.get("lat") is not None and nd.get("lng") is not None:
                    lat = float(nd["lat"])
                    lon = float(nd.get("lng") or nd.get("lon", 0))
                    if nd.get("timestamp"):
                        ts = nd["timestamp"]
                        last_updated_date = ts.strftime("%d-%b-%Y")
                        last_updated_time = ts.strftime("%I:%M:%S %p")
                        is_online_1m = (datetime.now() - ts).total_seconds() <= 60
            except Exception:
                pass

        if lat is None or lon is None:
            return jsonify({
                "error": "No GPS data available",
                "has_gps": False,
                "is_offline": is_power_off,
                "lat": None,
                "lng": None,
                "speed": "0",
                "last_updated_date": last_updated_date,
                "last_updated_time": last_updated_time,
                "is_power_off": is_power_off,
                "is_online_1m": is_online_1m,
                "camera_status": camera_status,
                "is_recording": is_recording,
                "trip_number": latest_trip_num,
                "trip_status": trip_status
            })

        # Return last known position with offline flag when device is not live
        if is_power_off:
            return jsonify({
                "has_gps": True,
                "is_offline": True,
                "lat": lat,
                "lng": lon,
                "speed": "0",
                "last_updated_date": last_updated_date,
                "last_updated_time": last_updated_time,
                "is_power_off": True,
                "is_online_1m": is_online_1m,
                "camera_status": camera_status,
                "is_recording": is_recording,
                "trip_number": latest_trip_num,
                "trip_status": trip_status
            })

        # === NEW: Map Recording (Live View) ===
        # Save it by default also!
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

        return jsonify({
            "lat": lat,
            "lng": lon,
            "speed": speed,
            "has_gps": True,
            "last_updated_date": last_updated_date,
            "last_updated_time": last_updated_time,
            "is_power_off": is_power_off,
            "is_online_1m": is_online_1m,
            "camera_status": camera_status,
            "is_recording": is_recording,
            "trip_number": latest_trip_num,
            "trip_status": trip_status
        })
    except Exception as e:
        print(f"Error fetching GPS for {device_id}: {str(e)}")
        return jsonify({"error": str(e), "has_gps": False}), 500


@app.route("/get_all_vehicle_locations")
def get_all_vehicle_locations():
    if session.get('user_type') != 'admin':
        return jsonify({"error": "Unauthorized"}), 403
    
    try:
        vehicles_list = get_processed_vehicles_list()
        return jsonify(vehicles_list)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/log_js_error", methods=["POST"])
def log_js_error():
    try:
        data = request.get_json() or {}
        print(f"❌ FRONTEND JS ERROR: {data.get('message')} at {data.get('source')}:{data.get('lineno')}:{data.get('colno')}\nStack: {data.get('error')}")
    except Exception as e:
        print(f"Error logging JS exception: {e}")
    return jsonify({"status": "logged"})


@app.route("/map_recording")
def map_recording():
    if session.get('user_type') != 'admin':
        return redirect(url_for('login'))
    return render_template_string(get_template("map_recording.html"))

@app.route("/api/get_devices_list")
def get_devices_list():
    if session.get('user_type') not in ('admin', 'truck', 'akhada', 'user'):
        return jsonify([])
    
    try:
        device_ids = set()
        
        # 1. From col_vehicles (registered_vehicles)
        for v in col_vehicles.find({}, {"device_id": 1}):
            d = v.get("device_id")
            if d:
                device_ids.add(d.strip())
                
        # 2. From new_devices
        try:
            from mongodb import mongo_client
            gps_db = mongo_client["gps_server_db"]
            for d in gps_db["new_devices"].distinct("truck_id"):
                if d:
                    device_ids.add(d.strip())
        except Exception as e:
            print(f"Error getting distinct new_devices: {e}")
            
        # 3. From assign_devices
        try:
            from mongodb import mongo_client
            gps_db = mongo_client["gps_server_db"]
            for d in gps_db["assign_devices"].distinct("truck_id"):
                if d:
                    device_ids.add(d.strip())
        except Exception as e:
            print(f"Error getting distinct assign_devices: {e}")
            
        # 4. From map_recordings
        try:
            for d in col_map_recordings.distinct("device_id"):
                if d:
                    device_ids.add(d.strip())
        except Exception as e:
            print(f"Error getting distinct map_recordings: {e}")

        # 5. From registered_trucks
        try:
            from mongodb import mongo_client
            gps_db = mongo_client["gps_server_db"]
            for d in gps_db["registered_trucks"].distinct("truck_id"):
                if d:
                    device_ids.add(d.strip())
        except Exception as e:
            print(f"Error getting distinct registered_trucks: {e}")
            
        return jsonify(sorted(list(device_ids)))
    except Exception as e:
        print(f"Error fetching device list: {e}")
        return jsonify([])

@app.route("/api/get_map_recordings_data")
def get_map_recordings_data():
    if session.get('user_type') not in ('admin', 'truck', 'akhada', 'user'):
        return jsonify({"error": "Unauthorized"}), 403
        
    device_id = request.args.get('device_id')
    date_str = request.args.get('date') # YYYY-MM-DD
    
    if not device_id or not date_str:
        return jsonify({"error": "Missing params"}), 400
        
    # Query mongo with projection for speed
    try:
        start_dt = datetime.strptime(date_str, "%Y-%m-%d")
        end_dt = start_dt + timedelta(days=1)
        
        # 1. Try exact match in map_recordings (fastest)
        query = {
            "device_id": device_id,
            "timestamp": {"$gte": start_dt, "$lt": end_dt}
        }
        cursor = list(col_map_recordings.find(
            query, {"lat": 1, "lng": 1, "speed": 1, "timestamp": 1, "_id": 0}
        ).sort("timestamp", 1))
        
        # 2. Try case-insensitive fallback in map_recordings
        if not cursor:
            query = {
                "device_id": {"$regex": f"^{device_id}$", "$options": "i"},
                "timestamp": {"$gte": start_dt, "$lt": end_dt}
            }
            cursor = list(col_map_recordings.find(
                query, {"lat": 1, "lng": 1, "speed": 1, "timestamp": 1, "_id": 0}
            ).sort("timestamp", 1))
            
        # 3. Try fallback in new_devices collection
        used_fallback = False
        if not cursor:
            from mongodb import mongo_client
            gps_db = mongo_client["gps_server_db"]
            # Try case-insensitive query in new_devices
            query = {
                "truck_id": {"$regex": f"^{device_id}$", "$options": "i"},
                "timestamp": {"$gte": start_dt, "$lt": end_dt}
            }
            cursor = list(gps_db["new_devices"].find(
                query, {"lat": 1, "lng": 1, "speed": 1, "timestamp": 1, "_id": 0}
            ).sort("timestamp", 1))
            if cursor:
                used_fallback = True

        total_points = len(cursor)

        points = []
        # Downsample if excessive (e.g. > 5000 points) to speed up transfer and UI rendering
        skip_n = 1
        if total_points > 10000: skip_n = 5
        elif total_points > 5000: skip_n = 2

        count = 0
        for doc in cursor:
            # Map lng/lng variations just in case
            lat_val = doc.get("lat")
            lng_val = doc.get("lng") or doc.get("lon")
            if not is_valid_gps_coordinate(lat_val, lng_val):
                continue
            count += 1
            if count % skip_n != 0: continue
            
            points.append({
                "lat": lat_val,
                "lng": lng_val,
                "speed": doc.get("speed"),
                "timestamp": doc["timestamp"].isoformat(),
                "time": doc["timestamp"].strftime("%H:%M:%S"),
                "ts": doc["timestamp"].timestamp()
            })
            
        points = filter_coordinate_spikes(points)
        return jsonify({
            "points": points, 
            "total_raw": total_points, 
            "optimized": skip_n > 1, 
            "fallback_used": used_fallback
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/recordings")
def recordings():
    if session.get('user_type') != 'admin':
        return redirect(url_for('login'))

    registered_map = {v.get("device_id"): v for v in col_vehicles.find() if v.get("device_id")}
    all_hardware_ids = list(registered_map.keys())

    # Status mapping: 1=Start, 2=Stop, 3=Load, 4=Unload
    status_map = {
        "1": "Start",
        "2": "Stop",
        "3": "Load",
        "4": "Unload"
    }

    devices_display_list = []
    for i, dev_id in enumerate(all_hardware_ids, start=1):
        # Get RFID data from MongoDB live GPS
        rfid_status = "N/A"
        current_uid = "N/A"

        try:
            device_data = get_live_gps(dev_id) or {}
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
    """Get camera information for a vehicle based on RTMP source preference"""
    if session.get('user_type') != 'admin':
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    try:
        # Get vehicle info from MongoDB
        vehicle_info = col_vehicles.find_one({"device_id": device_id})
        if not vehicle_info:
            # Fallback to case-insensitive match
            vehicle_info = col_vehicles.find_one({"device_id": {"$regex": f"^{re.escape(device_id)}$", "$options": "i"}})
            
        if not vehicle_info:
            # ESP truck — build minimal vehicle_info from assign_devices
            try:
                from mongodb import mongo_client
                assign_doc = mongo_client["gps_server_db"]["assign_devices"].find_one({"truck_id": {"$regex": f"^{device_id}$", "$options": "i"}})
            except Exception:
                assign_doc = None
            if assign_doc:
                vehicle_info = {
                    "device_id": device_id,
                    "mongo_rtmp": {
                        "rtmp1": assign_doc.get("front_rtmp", ""),
                        "rtmp2": assign_doc.get("rear_rtmp", ""),
                    },
                    "rtmp_source": "mongo",
                }
            else:
                return jsonify({"success": False, "error": "Vehicle not found"}), 404
        
        # Determine RTMP source preference
        source_pref = vehicle_info.get("rtmp_source", get_rtmp_source())
        
        def extract_stream_name(url, default_num):
            """Extract camera name from RTMP URL"""
            if not url:
                return None
            try:
                parts = url.strip().split('/')
                name = parts[-1] if parts else f"Camera {default_num}"
                return name if name and name.strip() else f"Camera {default_num}"
            except:
                return f"Camera {default_num}"
        
        cameras = []
        
        # Get camera links based on source preference
        if source_pref == 'mongo':
            # Use MongoDB camera links
            mongo_rtmp = vehicle_info.get("mongo_rtmp", {})
            for i in range(1, 5):
                rtmp_url = mongo_rtmp.get(f"rtmp{i}", "")
                if rtmp_url:
                    camera_name = extract_stream_name(rtmp_url, i)
                    cameras.append({
                        "id": str(i),
                        "name": camera_name
                    })
        else:
            # Fallback: use mongo_rtmp from vehicle info
            mongo_rtmp = vehicle_info.get("mongo_rtmp", {}) if vehicle_info else {}
            for i in range(1, 5):
                url = mongo_rtmp.get(f"rtmp{i}")
                if url:
                    name = extract_stream_name(url, i)
                    cameras.append({"id": str(i), "name": name})
        
        return jsonify({"success": True, "cameras": cameras, "source": source_pref})
        
    except Exception as e:
        print(f"Error in get_vehicle_cameras: {e}")
        import traceback
        traceback.print_exc()
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


@app.route("/api/export_recordings", methods=["GET", "POST"])
def api_export_recordings():
    if session.get('user_type') != 'admin':
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    map_image_base64 = None
    if request.method == "POST":
        data = request.json or {}
        device_id = data.get('vehicle')
        date_str = data.get('date')
        export_format = data.get('format', 'pdf')
        map_image_base64 = data.get('map_image')
    else:
        device_id = request.args.get('vehicle')
        date_str = request.args.get('date')
        export_format = request.args.get('format', 'csv')  # 'csv' or 'pdf'

    if not device_id or not date_str:
        return "Missing vehicle or date", 400

    try:
        # 1. Fetch Tracking Data from MongoDB
        try:
            start_dt = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            for fmt in ("%d/%m/%y", "%d/%m/%Y", "%d-%m-%Y"):
                try:
                    start_dt = datetime.strptime(date_str, fmt)
                    break
                except ValueError:
                    continue
            else:
                return "Invalid date format", 400
                
        end_dt = start_dt + timedelta(days=1)
        
        def fetch_cursor(s_dt, e_dt):
            # 1. Exact match in map_recordings
            query = {
                "device_id": device_id,
                "timestamp": {"$gte": s_dt, "$lt": e_dt}
            }
            points = list(col_map_recordings.find(
                query, {"lat": 1, "lng": 1, "speed": 1, "timestamp": 1, "_id": 0}
            ).sort("timestamp", 1))
            
            # 2. Case-insensitive fallback in map_recordings
            if not points:
                query = {
                    "device_id": {"$regex": f"^{device_id}$", "$options": "i"},
                    "timestamp": {"$gte": s_dt, "$lt": e_dt}
                }
                points = list(col_map_recordings.find(
                    query, {"lat": 1, "lng": 1, "speed": 1, "timestamp": 1, "_id": 0}
                ).sort("timestamp", 1))
                
            # 3. Fallback in new_devices
            if not points:
                try:
                    from mongodb import mongo_client
                    gps_db = mongo_client["gps_server_db"]
                    query = {
                        "truck_id": {"$regex": f"^{device_id}$", "$options": "i"},
                        "timestamp": {"$gte": s_dt, "$lt": e_dt}
                    }
                    new_pts = list(gps_db["new_devices"].find(
                        query, {"lat": 1, "lng": 1, "speed": 1, "timestamp": 1, "_id": 0}
                    ).sort("timestamp", 1))
                    
                    # Convert to standard format with lat and lng
                    points = []
                    for doc in new_pts:
                        lat_val = doc.get("lat")
                        lng_val = doc.get("lng") or doc.get("lon")
                        points.append({
                            "lat": lat_val,
                            "lng": lng_val,
                            "speed": doc.get("speed"),
                            "timestamp": doc["timestamp"]
                        })
                except Exception as e:
                    print(f"Error in export fallback to new_devices: {e}")
                    
            return [pt for pt in points if is_valid_gps_coordinate(pt.get("lat"), pt.get("lng"))]
            
        cursor = fetch_cursor(start_dt, end_dt)
        
        # Fallback to year 2026 if no data found in 2025
        if not cursor and start_dt.year == 2025:
            alt_start_dt = start_dt.replace(year=2026)
            alt_end_dt = alt_start_dt + timedelta(days=1)
            cursor = fetch_cursor(alt_start_dt, alt_end_dt)
            if cursor:
                start_dt = alt_start_dt
                end_dt = alt_end_dt
                date_str = start_dt.strftime("%Y-%m-%d")
        
        # Filter spikes (transient coordinate jumps)
        cursor = filter_coordinate_spikes(cursor)
        
        # 2. Prepare Report Data
        vh = col_vehicles.find_one({"device_id": device_id})
        rc_number = vh.get("rc_number", "N/A") if vh else "N/A"
        
        try:
            interval_min = int(request.args.get('interval', 1))
        except:
            interval_min = 1
            
        # Filter points by interval
        eligible_points = []
        last_recorded_time = None
        for doc in cursor:
            ct = doc["timestamp"]
            if last_recorded_time is None or (ct - last_recorded_time).total_seconds() >= interval_min * 60:
                eligible_points.append(doc)
                last_recorded_time = ct

        if not eligible_points:
            return f"No tracking data found for {device_id} on {date_str}", 404

        # Construct Report Rows (WITHOUT Location column)
        report_rows = []
        for doc in eligible_points:
            report_rows.append({
                "Device ID": device_id,
                "Vehicle RC": rc_number,
                "Date": doc["timestamp"].strftime("%Y-%m-%d"),
                "Time": doc["timestamp"].strftime("%H:%M:%S"),
                "Latitude": doc["lat"],
                "Longitude": doc["lng"],
                "Speed (km/h)": doc.get("speed", 0)
            })

        # Generate report based on format
        if export_format == 'pdf':
            # Generate Stopped & Runned PDF Report
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import A4
            from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import inch
            import io
            import math
            
            # 1. Calculate Stats and Stops on full cursor list
            total_pings = len(cursor)
            total_distance = 0.0
            speeds = []
            moving_speeds = []
            
            def safe_float(val):
                try:
                    return float(str(val).replace('"', '').replace("'", '').strip())
                except:
                    return 0.0
                    
            def haversine_distance(coord1, coord2):
                R = 6371.0 # km
                lat1, lon1 = math.radians(coord1[0]), math.radians(coord1[1])
                lat2, lon2 = math.radians(coord2[0]), math.radians(coord2[1])
                dlat = lat2 - lat1
                dlon = lon2 - lon1
                a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
                c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
                return R * c
                
            for i in range(total_pings):
                pt = cursor[i]
                speed = safe_float(pt.get('speed', 0))
                speeds.append(speed)
                if speed > 2.0:
                    moving_speeds.append(speed)
                    
                if i > 0:
                    prev = cursor[i-1]
                    dist = haversine_distance((prev['lat'], prev['lng']), (pt['lat'], pt['lng']))
                    total_distance += dist
                    
            max_speed = max(speeds) if speeds else 0.0
            avg_speed = sum(moving_speeds) / len(moving_speeds) if moving_speeds else 0.0
            
            # Stop detection
            stops = []
            current_stop = None
            SPEED_THRESHOLD = 2.0 # km/h
            MIN_STOP_DURATION = 120 # seconds (2 minutes)
            
            for i, pt in enumerate(cursor):
                speed = safe_float(pt.get('speed', 0))
                ts = pt['timestamp']
                
                if speed < SPEED_THRESHOLD:
                    if current_stop is None:
                        current_stop = {
                            'start_time': ts,
                            'end_time': ts,
                            'lat': pt['lat'],
                            'lng': pt['lng'],
                            'points': [pt]
                        }
                    else:
                        current_stop['end_time'] = ts
                        current_stop['points'].append(pt)
                else:
                    if current_stop:
                        duration = (current_stop['end_time'] - current_stop['start_time']).total_seconds()
                        if duration >= MIN_STOP_DURATION:
                            avg_lat = sum(p['lat'] for p in current_stop['points']) / len(current_stop['points'])
                            avg_lng = sum(p['lng'] for p in current_stop['points']) / len(current_stop['points'])
                            stops.append({
                                'start_time': current_stop['start_time'],
                                'end_time': current_stop['end_time'],
                                'duration': int(duration),
                                'lat': avg_lat,
                                'lng': avg_lng
                            })
                        current_stop = None
                        
            if current_stop:
                duration = (current_stop['end_time'] - current_stop['start_time']).total_seconds()
                if duration >= MIN_STOP_DURATION:
                    avg_lat = sum(p['lat'] for p in current_stop['points']) / len(current_stop['points'])
                    avg_lng = sum(p['lng'] for p in current_stop['points']) / len(current_stop['points'])
                    stops.append({
                        'start_time': current_stop['start_time'],
                        'end_time': current_stop['end_time'],
                        'duration': int(duration),
                        'lat': avg_lat,
                        'lng': avg_lng
                    })
                    
            total_stop_duration = sum(s['duration'] for s in stops)
            stops_count = len(stops)
            runs_count = stops_count + 1
            
            # Fetch vehicle assignment info
            vh = col_vehicles.find_one({"device_id": device_id}) or {}
            driver_name = vh.get("driver_name") or "Pending/Unassigned"
            transporter_name = vh.get("transporter_name") or "Unassigned"
            godown_manager = vh.get("godown_manager") or "Unassigned"
            rc_number = vh.get("rc_number") or "N/A"
            
            # 2. Setup ReportLab A4 Portrait Document
            buffer = io.BytesIO()
            doc = SimpleDocTemplate(
                buffer,
                pagesize=A4, 
                rightMargin=36,
                leftMargin=36, 
                topMargin=36,
                bottomMargin=36
            )
            
            elements = []
            styles = getSampleStyleSheet()
            
            # Define Custom Styles
            title_style = ParagraphStyle(
                'ReportTitle',
                parent=styles['Normal'],
                fontName='Helvetica-Bold',
                fontSize=18,
                leading=22,
                textColor=colors.HexColor('#0f172a'),
                alignment=0, # Left
                spaceAfter=4
            )
            
            subtitle_style = ParagraphStyle(
                'ReportSubtitle',
                parent=styles['Normal'],
                fontName='Helvetica',
                fontSize=8.5,
                leading=12,
                textColor=colors.HexColor('#475569'),
                spaceAfter=12
            )
            
            section_style = ParagraphStyle(
                'ReportSection',
                parent=styles['Normal'],
                fontName='Helvetica-Bold',
                fontSize=11,
                leading=15,
                textColor=colors.HexColor('#1e293b'),
                spaceBefore=14,
                spaceAfter=6
            )
            
            grid_key_style = ParagraphStyle(
                'GridKey',
                parent=styles['Normal'],
                fontName='Helvetica-Bold',
                fontSize=8,
                leading=10,
                textColor=colors.HexColor('#475569')
            )
            
            grid_val_style = ParagraphStyle(
                'GridVal',
                parent=styles['Normal'],
                fontName='Helvetica',
                fontSize=8.5,
                leading=11,
                textColor=colors.HexColor('#0f172a')
            )
            
            th_style = ParagraphStyle(
                'TableHeader',
                parent=styles['Normal'],
                fontName='Helvetica-Bold',
                fontSize=8.5,
                leading=11,
                textColor=colors.whitesmoke,
                alignment=1
            )
            
            td_style = ParagraphStyle(
                'TableCell',
                parent=styles['Normal'],
                fontName='Helvetica',
                fontSize=8,
                leading=10,
                alignment=1
            )

            td_left_style = ParagraphStyle(
                'TableCellLeft',
                parent=styles['Normal'],
                fontName='Helvetica',
                fontSize=8.5,
                leading=12,
                alignment=0
            )
            
            badge_short = ParagraphStyle(
                'BadgeShort',
                parent=styles['Normal'],
                fontName='Helvetica-Bold',
                fontSize=7.5,
                leading=9,
                textColor=colors.HexColor('#15803d'),
                alignment=1
            )
            badge_medium = ParagraphStyle(
                'BadgeMedium',
                parent=styles['Normal'],
                fontName='Helvetica-Bold',
                fontSize=7.5,
                leading=9,
                textColor=colors.HexColor('#c2410c'),
                alignment=1
            )
            badge_long = ParagraphStyle(
                'BadgeLong',
                parent=styles['Normal'],
                fontName='Helvetica-Bold',
                fontSize=7.5,
                leading=9,
                textColor=colors.HexColor('#b91c1c'),
                alignment=1
            )
            
            # Sleek primary accent top border bar
            header_bar = Table([[""]], colWidths=[520], rowHeights=[4])
            header_bar.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#2563eb')),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 0),
            ]))
            elements.append(header_bar)
            elements.append(Spacer(1, 10))
            
            # Report title
            elements.append(Paragraph("VEHICLE STOP & RUN AUDIT REPORT", title_style))
            gen_time_str = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
            elements.append(Paragraph(f"Official supply chain audit log • Generated on {gen_time_str}", subtitle_style))
            
            # Section: Performance summary
            elements.append(Paragraph("1. Trip Performance Summary Grid", section_style))
            
            summary_data = [
                [
                    Paragraph("Registered Vehicle", grid_key_style), Paragraph(f"{device_id} ({rc_number})", grid_val_style),
                    Paragraph("Audit Date", grid_key_style), Paragraph(date_str, grid_val_style)
                ],
                [
                    Paragraph("Assigned Driver", grid_key_style), Paragraph(driver_name, grid_val_style),
                    Paragraph("Total Distance", grid_key_style), Paragraph(f"{total_distance:.2f} km", grid_val_style)
                ],
                [
                    Paragraph("Transporter Partner", grid_key_style), Paragraph(transporter_name, grid_val_style),
                    Paragraph("Max / Avg Speed", grid_key_style), Paragraph(f"{max_speed:.1f} km/h / {avg_speed:.1f} km/h", grid_val_style)
                ],
                [
                    Paragraph("Godown Manager", grid_key_style), Paragraph(godown_manager, grid_val_style),
                    Paragraph("Stopped & Runned Stats", grid_key_style), Paragraph(f"{stops_count} Stops ({runs_count} Runs)", grid_val_style)
                ],
                [
                    Paragraph("Total GPS Records", grid_key_style), Paragraph(str(total_pings), grid_val_style),
                    Paragraph("Total Stopped Time", grid_key_style), Paragraph(f"{total_stop_duration // 60}m {total_stop_duration % 60}s", grid_val_style)
                ]
            ]
            
            summary_table = Table(summary_data, colWidths=[120, 140, 120, 140])
            summary_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f8fafc')),
                ('BACKGROUND', (2, 0), (2, -1), colors.HexColor('#f8fafc')),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ]))
            elements.append(summary_table)
            
            # Section 2: Visual Route & Stops Map
            elements.append(Paragraph("2. Route & Stops Shape Projection Map", section_style))
            
            map_inserted = False
            
            if map_image_base64 and "," in map_image_base64:
                try:
                    import base64
                    from reportlab.platypus import Image
                    img_data = base64.b64decode(map_image_base64.split(",")[1])
                    img_buffer = io.BytesIO(img_data)
                    # Create a beautiful ReportLab flowable image sized 520x230 to fit A4 perfectly
                    map_image_flowable = Image(img_buffer, width=520, height=230)
                    elements.append(map_image_flowable)
                    map_inserted = True
                except Exception as e:
                    print(f"Error decoding base64 map image: {e}")
                    
            if not map_inserted:
                # Vector Map Drawing Fallback
                from reportlab.graphics.shapes import Drawing, Rect, String, Circle, Line
                map_draw = Drawing(520, 230)
                # Background card styling
                map_draw.add(Rect(0, 0, 520, 230, fillColor=colors.HexColor('#f8fafc'), strokeColor=colors.HexColor('#e2e8f0'), strokeWidth=1, rx=8, ry=8))
                
                # Map Label
                map_draw.add(String(12, 12, "Route Shape Visualizer (Preserved True Aspect Ratio)", fontName="Helvetica-Bold", fontSize=8, fillColor=colors.HexColor('#64748b')))
                
                if total_pings > 0:
                    lats = [pt['lat'] for pt in cursor]
                    lngs = [pt['lng'] for pt in cursor]
                    min_lat, max_lat = min(lats), max(lats)
                    min_lng, max_lng = min(lngs), max(lngs)
                    
                    lat_range = max_lat - min_lat
                    lng_range = max_lng - min_lng
                    
                    if lat_range == 0: lat_range = 0.0001
                    if lng_range == 0: lng_range = 0.0001
                    
                    # Fit map in a 460x160 bounding box with padding
                    scale_x = 460.0 / lng_range
                    scale_y = 160.0 / lat_range
                    scale = min(scale_x, scale_y)
                    
                    # Center the projected elements inside 520x230 card
                    x_offset = 30.0 + (460.0 - lng_range * scale) / 2.0
                    y_offset = 35.0 + (160.0 - lat_range * scale) / 2.0
                    
                    def project(lat, lng):
                        x = x_offset + (lng - min_lng) * scale
                        y = y_offset + (lat - min_lat) * scale
                        return x, y
                        
                    projected_points = [project(pt['lat'], pt['lng']) for pt in cursor]
                    
                    # Draw route segment-by-segment
                    for i in range(1, len(projected_points)):
                        x1, y1 = projected_points[i-1]
                        x2, y2 = projected_points[i]
                        map_draw.add(Line(x1, y1, x2, y2, strokeColor=colors.HexColor('#2563eb'), strokeWidth=2.8))
                        
                    # Draw Start Marker
                    start_x, start_y = projected_points[0]
                    map_draw.add(Circle(start_x, start_y, 7, fillColor=colors.HexColor('#22c55e'), strokeColor=colors.white, strokeWidth=1.5))
                    map_draw.add(String(start_x + 9, start_y - 3, "START", fontName="Helvetica-Bold", fontSize=7, fillColor=colors.HexColor('#15803d')))
                    
                    # Draw End Marker
                    if len(projected_points) > 1:
                        end_x, end_y = projected_points[-1]
                        map_draw.add(Circle(end_x, end_y, 7, fillColor=colors.HexColor('#ef4444'), strokeColor=colors.white, strokeWidth=1.5))
                        map_draw.add(String(end_x + 9, end_y - 3, "END", fontName="Helvetica-Bold", fontSize=7, fillColor=colors.HexColor('#b91c1c')))
                        
                    # Draw Stops
                    for idx, stop in enumerate(stops):
                        stop_x, stop_y = project(stop['lat'], stop['lng'])
                        duration = stop['duration']
                        risk = 'High' if duration > 900 else ('Medium' if duration >= 300 else 'Low')
                        stop_color = colors.HexColor('#ef4444') if risk == 'High' else (colors.HexColor('#f97316') if risk == 'Medium' else colors.HexColor('#22c55e'))
                        
                        map_draw.add(Circle(stop_x, stop_y, 6, fillColor=stop_color, strokeColor=colors.white, strokeWidth=1.2))
                        map_draw.add(String(stop_x - 3, stop_y + 8, f"#{idx+1}", fontName="Helvetica-Bold", fontSize=7.5, fillColor=colors.HexColor('#0f172a')))
                
                elements.append(map_draw)
                
            elements.append(Spacer(1, 10))
            
            # Section 3: Stopped & Runned Logs Table
            elements.append(Paragraph("3. Stopped & Runned Logs Table", section_style))
            
            if stops_count == 0:
                no_stops_data = [[
                    Paragraph("<font color='#15803d'><b>✔ Continuous Movement Node (No Pauses Logs)</b></font><br/>The vehicle operated continuously without any static pauses (duration >= 2m, speed < 2.0 km/h) recorded during this travel window.", td_left_style)
                ]]
                no_stops_table = Table(no_stops_data, colWidths=[520])
                no_stops_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f0fdf4')),
                    ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#bbf7d0')),
                    ('TOPPADDING', (0, 0), (-1, -1), 12),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
                    ('LEFTPADDING', (0, 0), (-1, -1), 16),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 16),
                ]))
                elements.append(no_stops_table)
            else:
                stops_table_data = [[
                    Paragraph("Stop #", th_style),
                    Paragraph("Stop Type", th_style),
                    Paragraph("Duration", th_style),
                    Paragraph("Period (Arrival to Departure)", th_style),
                    Paragraph("Location Coordinates", th_style)
                ]]
                
                def format_duration(seconds):
                    if seconds >= 60:
                        return f"{seconds // 60}m {seconds % 60}s"
                    return f"{seconds}s"
                    
                def format_time_str(dt):
                    return dt.strftime("%I:%M:%S %p")
                    
                t_style = [
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0f172a')), # Slate-900 header
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
                    ('TOPPADDING', (0, 0), (-1, -1), 5),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                ]
                
                for idx, stop in enumerate(stops):
                    row_idx = idx + 1
                    duration = stop['duration']
                    risk = 'High' if duration > 900 else ('Medium' if duration >= 300 else 'Low')
                    severity_str = 'Long Stop' if risk == 'High' else ('Medium Stop' if risk == 'Medium' else 'Short Stop')
                    
                    badge_style = badge_long if risk == 'High' else (badge_medium if risk == 'Medium' else badge_short)
                    badge_color_hex = '#fee2e2' if risk == 'High' else ('#ffedd5' if risk == 'Medium' else '#dcfce7')
                    
                    # Apply background color only to the badge cell
                    t_style.append(('BACKGROUND', (1, row_idx), (1, row_idx), colors.HexColor(badge_color_hex)))
                    if row_idx % 2 == 0:
                        t_style.append(('BACKGROUND', (0, row_idx), (0, row_idx), colors.HexColor('#f8fafc')))
                        t_style.append(('BACKGROUND', (2, row_idx), (-1, row_idx), colors.HexColor('#f8fafc')))
                        
                    row = [
                        Paragraph(f"<b>Stop #{idx + 1}</b>", td_style),
                        Paragraph(severity_str, badge_style),
                        Paragraph(format_duration(duration), td_style),
                        Paragraph(f"{format_time_str(stop['start_time'])} to {format_time_str(stop['end_time'])}", td_style),
                        Paragraph(f"<a href='https://www.google.com/maps?q={stop['lat']},{stop['lng']}'><font color='#2563eb'><u>{stop['lat']:.5f}, {stop['lng']:.5f}</u></font></a>", td_style)
                    ]
                    stops_table_data.append(row)
                    
                stops_table = Table(stops_table_data, colWidths=[45, 90, 85, 180, 120])
                stops_table.setStyle(TableStyle(t_style))
                elements.append(stops_table)
                
            # Build A4 PDF
            doc.build(elements)
            
            buffer.seek(0)
            filename = f"Vehicle_Stop_Run_Report_{device_id}_{date_str}.pdf"
            return Response(
                buffer.getvalue(),
                mimetype="application/pdf",
                headers={"Content-disposition": f"attachment; filename={filename}"}
            )
        else:
            # Generate CSV
            import io, csv
            output = io.StringIO()
            fieldnames = ["Device ID", "Vehicle RC", "Date", "Time", "Latitude", "Longitude", "Speed (km/h)"]
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(report_rows)
            
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
@app.route("/api/video/view")
def api_server_file_handler():
    if session.get('user_type') != 'admin':
        return "Unauthorized", 403
    
    path = request.args.get("path", "").strip("/")
    name = request.args.get("name", "")
    mode = request.args.get("mode", "remote")
    is_download = "download" in request.path
    
    if not name or ".." in path or ".." in name:
        return "Invalid request", 400
    
    base_path = SSH_BASE_PATH if mode == "remote" else LOCAL_BASE_PATH
    full_path = os.path.join(base_path, path, name).replace("\\", "/")
    
    # Determine mimetype
    ext = name.split('.')[-1].lower()
    mimetype = 'video/mp4' # default
    if ext == 'ts': mimetype = 'video/mp2t'
    elif ext == 'mkv': mimetype = 'video/x-matroska'
    elif ext == 'avi': mimetype = 'video/x-msvideo'

    try:
        if mode == "remote":
            client = get_ssh_client()
            sftp = client.open_sftp()
            
            # Get File Stats for Range Support
            stat = sftp.stat(full_path)
            file_size = stat.st_size
            
            # Parse Range Header
            range_header = request.headers.get('Range', None)
            start = 0
            end = file_size - 1
            status_code = 200
            
            if range_header:
                match = re.search(r'bytes=(\d+)-(\d*)', range_header)
                if match:
                    start = int(match.group(1))
                    if match.group(2):
                        end = int(match.group(2))
                    status_code = 206
            
            content_length = end - start + 1
            
            def stream_remote(start_pos, length):
                try:
                    remote_file = sftp.open(full_path, 'rb')
                    remote_file.seek(start_pos)
                    remaining = length
                    while remaining > 0:
                        chunk_size = min(remaining, 1024 * 1024)
                        chunk = remote_file.read(chunk_size)
                        if not chunk: break
                        yield chunk
                        remaining -= len(chunk)
                    remote_file.close()
                finally:
                    sftp.close()
                    client.close()

            headers = {
                'Accept-Ranges': 'bytes',
                'Content-Length': str(content_length),
            }
            if status_code == 206:
                headers['Content-Range'] = f'bytes {start}-{end}/{file_size}'
            
            if is_download:
                headers["Content-Disposition"] = f"attachment; filename={name}"
                
            return Response(stream_remote(start, content_length), status=status_code, mimetype=mimetype, headers=headers)
        else:
            return send_from_directory(
                os.path.dirname(full_path), 
                os.path.basename(full_path), 
                as_attachment=is_download,
                mimetype=mimetype,
                conditional=True
            )
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

        vehicles_display = []

        # Show all registered devices
        for reg_info in registered_vehicles:
            device_id = reg_info.get("device_id")
            if not device_id:
                continue

            # Use per-vehicle source preference if set, otherwise use global source
            vehicle_source = reg_info.get("rtmp_source", source)

            # Get location and status from MongoDB live GPS
            live_gps = get_live_gps(device_id) or {}
            location = live_gps
            last_updated_str = "N/A"
            is_power_off = True

            try:
                date_str = location.get("date", "")
                time_str = location.get("time", "")
                if date_str and time_str:
                    utc_time = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M:%S")
                    ist_time = utc_time
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
            firebase_links = {}
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

        # Always save to MongoDB
        col_vehicles.update_one(
            {"device_id": device_id},
            {"$set": {"mongo_rtmp": payload, "rtmp_source": "mongo"}},
            upsert=True
        )
        return jsonify({"success": True, "message": "RTMP links updated successfully"})
            
    except Exception as e:
        print(f"Error saving RTMP: {e}")
        return jsonify({"success": False, "message": str(e)}), 500



def monitor_rfid_for_auto_recording():
    print("🚀 RFID Monitor Thread Started")
    while RFID_MONITOR_ACTIVE:
        try:
            # Fetch all devices from MongoDB
            all_device_ids = [v.get("device_id") for v in col_vehicles.find({}, {"device_id": 1}) if v.get("device_id")]
            all_devices = {did: get_live_gps(did) for did in all_device_ids}

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
            # 1. Fetch all devices from MongoDB
            try:
                all_device_ids = [v.get("device_id") for v in col_vehicles.find({}, {"device_id": 1}) if v.get("device_id")]
                all_devices = {did: get_live_gps(did) for did in all_device_ids}
            except Exception as e:
                print(f"Global GPS Monitor Fetch Error: {e}")
                time.sleep(10)
                continue

            current_time = datetime.now()

            for device_id, device_data in all_devices.items():
                if not isinstance(device_data, dict): continue

                # Flat structure — use device_data directly
                loc = device_data
                if not loc: continue

                lat = loc.get('lat')
                lng = loc.get('lng') or loc.get('lon')
                speed = loc.get('speed', 0)
                
                if lat is None or lng is None: continue
                try:
                    lat = float(lat)
                    lng = float(lng)
                except: continue
                
                # Skip invalid / out-of-bounds coordinates
                if not is_valid_gps_coordinate(lat, lng): continue
                
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
                # Fetch GPS data from MongoDB
                device_data = get_live_gps(device_name) or {}

                if device_data:
                    # Extract GPS and device information
                    rfid_data = device_data.get('rfid_data', {})

                    # Validate GPS coordinates before recording
                    lat_val = device_data.get('lat', 0)
                    lng_val = device_data.get('lng', 0) or device_data.get('lon', 0)
                    if not is_valid_gps_coordinate(lat_val, lng_val):
                        # Skip this ping if it is invalid/out-of-bounds
                        stop_flag.wait(3)
                        continue
                    
                    # Create GPS record
                    gps_record = {
                        'device_name': device_name,
                        'date': date_str,
                        'session_number': session_number,
                        'timestamp': datetime.now().isoformat(),
                        'location': {
                            'latitude': device_data.get('lat', 0),
                            'longitude': device_data.get('lng', 0) or device_data.get('lon', 0),
                            'altitude': device_data.get('alt', 0),
                            'speed': device_data.get('speed', 0),
                            'satellites': device_data.get('sat', 0),
                            'gps_date': device_data.get('date', ''),
                            'gps_time': device_data.get('time', ''),
                            'uid': device_data.get('UID', '')
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
        
        # Get camera URLs from MongoDB
        vehicle_doc = col_vehicles.find_one({"device_id": device_name}) or {}
        mongo_rtmp = vehicle_doc.get("mongo_rtmp", {})
        device_data = mongo_rtmp

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
                live_doc = get_live_gps(device_name) or {}
                current_status = str(live_doc.get('rfid_data', {}).get('status', ''))
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
        
        # Get camera URLs from MongoDB
        print(f"📡 Fetching camera data from MongoDB...")
        try:
            vehicle_doc_rec = col_vehicles.find_one({"device_id": device_name}) or {}
            device_data = vehicle_doc_rec.get("mongo_rtmp", {})
            print(f"✅ MongoDB data retrieved")
        except Exception as e:
            print(f"❌ MongoDB error: {str(e)}")
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
        # Fetch current RFID status from MongoDB
        device_data = get_live_gps(device_name) or {}

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
    from mongodb import mongo_client
    assign_col = mongo_client["gps_server_db"]["assign_devices"]
    roles_data = {"SUPER_ADMIN": [], "RATH_USER": [], "AKHADA_USER": [], "TRUCK_USER": [], "MAINTENANCE_USER": []}
    for doc in assign_col.find():
        role = doc.get("role", "")
        if role in roles_data:
            roles_data[role].append({
                "username": doc.get("username", ""),
                "truck_id": doc.get("truck_id", ""),
                "assigned_at": str(doc.get("assigned_at", ""))
            })
    return render_template_string(get_template("LIST_ROLES_HTML"), roles_data=roles_data, logo_url=LOGO_URL)


# ── GROUPING ROUTES ────────────────────────────────────────────────────────────
@app.route("/grouping")
def grouping():
    if session.get('user_type') != 'admin': return redirect(url_for('login'))
    from mongodb import mongo_client
    gps_db = mongo_client["gps_server_db"]
    akhadas = list(gps_db["assign_devices"].find({"role": "AKHADA_USER"}, {"_id": 0}))
    trucks  = list(gps_db["assign_devices"].find({"role": "TRUCK_USER"},  {"_id": 0}))
    groups  = {d["akhada_truck_id"]: d.get("assigned_trucks", [])
               for d in gps_db["akhada_groups"].find({}, {"_id": 0})}
    return render_template_string(get_template("GROUPING_HTML"), akhadas=akhadas, trucks=trucks,
                           groups=groups, logo_url=LOGO_URL)

@app.route("/akhada_test_login", methods=["GET", "POST"])
def akhada_test_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        from mongodb import mongo_client
        results = []
        for doc in mongo_client["gps_server_db"]["assign_devices"].find({"role": "AKHADA_USER"}):
            su = str(doc.get("username", "")).strip()
            sp = str(doc.get("password", "")).strip()
            match = (su.lower() == username.lower() and sp == password)
            results.append({"stored_user": su, "stored_pass": sp, "input_user": username, "input_pass": password, "match": match})
            if match:
                session.clear()
                session['user_type'] = 'akhada'
                session['akhada_username'] = su
                session['akhada_truck_id'] = str(doc.get("truck_id", ""))
                session.modified = True
                return f"<h2>LOGIN OK</h2><p>user_type=akhada username={su}</p><p>Session: {dict(session)}</p><a href='/akhada_dashboard'>Go to Dashboard &rarr;</a>"
        return f"<h2>LOGIN FAILED</h2><pre>{results}</pre>"
    return '<form method=POST>user:<input name=username> pass:<input name=password type=password><button>Test</button></form>'

@app.route("/api/mongo_health")
def mongo_health():
    if session.get('user_type') != 'admin': return jsonify({"error": "Unauthorized"}), 403
    try:
        from mongodb import mongo_client
        db = mongo_client["gps_server_db"]
        needed = ["assign_devices","registered_trucks","new_devices","akhada_groups",
                  "registered_vehicles","godown_managers","transporters","drivers",
                  "gps_live","sos_logs","map_recordings"]
        counts = {}
        for c in needed:
            try: counts[c] = db[c].count_documents({})
            except Exception as e: counts[c] = f"error: {e}"
        # sample assign_devices
        sample = list(db["assign_devices"].find({}, {"_id":0,"truck_id":1,"username":1,"role":1}).limit(5))
        import shutil
        ffmpeg_path = shutil.which("ffmpeg")
        return jsonify({"ok": True, "counts": counts, "assign_devices_sample": sample, "ffmpeg": ffmpeg_path or "NOT FOUND"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/debug_akhada_users")
def debug_akhada_users():
    if session.get('user_type') != 'admin': return jsonify({"error": "Unauthorized"}), 403
    from mongodb import mongo_client
    users = []
    for doc in mongo_client["gps_server_db"]["assign_devices"].find({"role": "AKHADA_USER"}):
        pw = doc.get("password", "")
        users.append({
            "truck_id": doc.get("truck_id"),
            "username": doc.get("username"),
            "password_repr": repr(str(pw)),
        })
    return jsonify(users)

@app.route("/api/get_password/<truck_id>")
def get_password(truck_id):
    if session.get('user_type') != 'admin': return jsonify({"ok": False}), 403
    from mongodb import mongo_client
    doc = mongo_client["gps_server_db"]["assign_devices"].find_one({"truck_id": {"$regex": f"^{truck_id}$", "$options": "i"}}, {"password": 1})
    return jsonify({"ok": bool(doc), "password": doc.get("password", "") if doc else ""})

@app.route("/api/change_password", methods=["POST"])
def change_password():
    if session.get('user_type') != 'admin': return jsonify({"ok": False}), 403
    data = request.get_json(silent=True) or {}
    truck_id = str(data.get("truck_id", "")).strip()
    new_pw   = str(data.get("password", "")).strip()
    if not truck_id or not new_pw: return jsonify({"ok": False, "error": "Missing fields"}), 400
    from mongodb import mongo_client
    mongo_client["gps_server_db"]["assign_devices"].update_one(
        {"truck_id": truck_id}, {"$set": {"password": new_pw}}
    )
    return jsonify({"ok": True})

@app.route("/api/grouping/save", methods=["POST"])
def save_grouping():
    if session.get('user_type') != 'admin': return jsonify({"ok": False}), 403
    data = request.get_json(silent=True) or {}
    akhada_id      = str(data.get("akhada_truck_id", "")).strip()
    assigned_trucks = data.get("assigned_trucks", [])
    if not akhada_id: return jsonify({"ok": False, "error": "akhada_truck_id required"}), 400
    from mongodb import mongo_client
    mongo_client["gps_server_db"]["akhada_groups"].update_one(
        {"akhada_truck_id": akhada_id},
        {"$set": {"akhada_truck_id": akhada_id, "assigned_trucks": assigned_trucks, "updated_at": datetime.now()}},
        upsert=True
    )
    return jsonify({"ok": True})

@app.route("/api/grouping/<akhada_id>")
def get_grouping(akhada_id):
    from mongodb import mongo_client
    doc = mongo_client["gps_server_db"]["akhada_groups"].find_one(
        {"akhada_truck_id": akhada_id}, {"_id": 0}
    )
    return jsonify({"ok": bool(doc), "assigned_trucks": doc.get("assigned_trucks", []) if doc else []})

# ── AKHADA LOGIN & DASHBOARD ───────────────────────────────────────────────────
@app.route("/akhada_login", methods=["GET", "POST"])
def akhada_login():
    if session.get('user_type') == 'akhada':
        return redirect(url_for('akhada_dashboard'))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        try:
            from mongodb import mongo_client
            for doc in mongo_client["gps_server_db"]["assign_devices"].find({"role": "AKHADA_USER"}):
                su = str(doc.get("username", "")).strip()
                sp = str(doc.get("password", "")).strip()
                if su.lower() == username.lower() and sp == password:
                    session.clear()
                    session['user_type']       = 'akhada'
                    session['akhada_username'] = su
                    session['akhada_truck_id'] = str(doc.get("truck_id", ""))
                    session.modified = True
                    return redirect('/akhada_dashboard')
            error = f"No match. Tried username='{username}' password='{password}'"
        except Exception as e:
            error = f"DB error: {e}"
    login_html = get_template("AKHADA_LOGIN_HTML")
    return render_template_string(login_html, logo_url=LOGO_URL, error=error)

@app.route("/akhada_dashboard")
def akhada_dashboard():
    if session.get('user_type') != 'akhada':
        return redirect(url_for('akhada_login'))
    akhada_truck_id = session.get('akhada_truck_id', '')
    from mongodb import mongo_client
    gps_db = mongo_client["gps_server_db"]
    # Own info
    own_doc = gps_db["assign_devices"].find_one({"truck_id": {"$regex": f"^{akhada_truck_id}$", "$options": "i"}}, {"_id": 0, "password": 0}) or {}
    # Grouped trucks
    group_doc = gps_db["akhada_groups"].find_one({"akhada_truck_id": akhada_truck_id}) or {}
    truck_ids = group_doc.get("assigned_trucks", [])
    trucks = []
    latest_map = {}
    for nd in gps_db["new_devices"].find({"truck_id": {"$in": truck_ids}}).sort("timestamp", -1):
        tid = nd.get("truck_id")
        if tid and tid not in latest_map:
            latest_map[tid] = nd
    assign_map = {d["truck_id"]: d for d in gps_db["assign_devices"].find({"truck_id": {"$in": truck_ids}}, {"_id": 0, "password": 0})}
    for tid in truck_ids:
        nd = latest_map.get(tid, {})
        a  = assign_map.get(tid, {})
        ts = nd.get("timestamp")
        trucks.append({
            "truck_id": tid,
            "lat": nd.get("lat"), "lng": nd.get("lng"),
            "speed": nd.get("speed", 0),
            "motion": nd.get("motion", "unknown"),
            "last_seen": ts.strftime("%d-%b-%Y %I:%M %p") if ts else "N/A",
            "driver": a.get("driver_name", ""),
            "plate": a.get("vehicle_plate", ""),
        })
    return render_template_string(get_template("AKHADA_DASHBOARD_HTML"),
                           own=own_doc, trucks=trucks,
                           akhada_truck_id=akhada_truck_id, logo_url=LOGO_URL)

@app.route("/akhada_logout")
def akhada_logout():
    session.clear()
    return redirect(url_for('login'))

@app.route("/truck_dashboard")
def truck_dashboard():
    if session.get('user_type') != 'truck':
        return redirect(url_for('login'))
    truck_id = session.get('truck_id', '')
    try:
        from mongodb import mongo_client
        doc = mongo_client["gps_server_db"]["assign_devices"].find_one({"truck_id": {"$regex": f"^{truck_id}$", "$options": "i"}}, {"_id": 0, "password": 0})
    except Exception:
        doc = {}
    return render_template_string(get_template("TRUCK_DASHBOARD_HTML"), truck_id=truck_id, doc=doc or {}, logo_url=LOGO_URL)

@app.route("/truck_logout")
def truck_logout():
    session.clear()
    return redirect(url_for('login'))


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
    if 'username' not in session and session.get('user_type') not in ('truck', 'akhada'):
        return redirect(url_for('login'))

    # Check if user has permission to view this device
    username = session['username']
    role = session.get('role')
    user_doc, _ = find_user_in_db(username)

    if not user_doc and role != 'Administrator' and session.get('user_type') not in ('truck', 'akhada'):
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
            # allow ESP trucks — checked again below with assign_devices
            pass

        if role == 'Transporter' and vehicle_info.get("transporter_name") != user_doc.get("name"):
            flash("You don't have permission to view this device.", "error")
            return redirect(url_for('user_dashboard'))

        if role == 'Driver' and vehicle_info.get("driver_name") != user_doc.get("name"):
            flash("You don't have permission to view this device.", "error")
            return redirect(url_for('user_dashboard'))

    try:
        device_data = get_live_gps(device_id) or {}
        # Fallback to new_devices if get_live_gps has no valid coords
        loc = device_data.get("location", {})
        lat = loc.get("lat") or loc.get("latitude")
        lng = loc.get("lng") or loc.get("longitude") or loc.get("lon")
        if not lat or not lng or float(lat) == 0 or float(lng) == 0:
            try:
                from mongodb import mongo_client
                nd = mongo_client["gps_server_db"]["new_devices"].find_one(
                    {"truck_id": {"$regex": f"^{device_id}$", "$options": "i"}, "lat": {"$exists": True, "$ne": 0}},
                    sort=[("timestamp", -1)]
                )
                if nd and nd.get("lat") and nd.get("lng"):
                    device_data["location"] = {
                        "lat": float(nd["lat"]),
                        "lng": float(nd["lng"]),
                        "speed": float(nd.get("speed", 0)),
                        "alt": 0,
                        "sat": 0,
                        "date": nd["timestamp"].strftime("%d-%m-%Y") if nd.get("timestamp") else "--",
                        "time": nd["timestamp"].strftime("%H:%M:%S") if nd.get("timestamp") else "--",
                        "UID": "GPS"
                    }
            except Exception as ex:
                print(f"Fallback error in device_info: {ex}")
        vehicle_info_local = col_vehicles.find_one({"device_id": device_id})
        if not vehicle_info_local:
            # ESP truck — build minimal vehicle_info from assign_devices
            try:
                from mongodb import mongo_client
                assign_doc = mongo_client["gps_server_db"]["assign_devices"].find_one({"truck_id": {"$regex": f"^{device_id}$", "$options": "i"}})
            except Exception:
                assign_doc = None
            vehicle_info_local = {
                "device_id": device_id,
                "rc_number": assign_doc.get("vehicle_plate", "") if assign_doc else "",
                "driver_name": assign_doc.get("driver_name", "") if assign_doc else "",
                "transporter_name": assign_doc.get("contractor_name", "") if assign_doc else "",
                "godown_manager": "",
                "mongo_rtmp": {
                    "rtmp1": assign_doc.get("front_rtmp", "") if assign_doc else "",
                    "rtmp2": assign_doc.get("rear_rtmp", "") if assign_doc else "",
                },
                "rtmp_source": "mongo",
            }
            vehicle_info = vehicle_info_local
        device_data = sanitize_data(device_data)
    except Exception as e:
        return f"Connection Error: {e}", 500

    def extract_stream_name(url, default_num):
        if not url: return f"Camera {default_num}"
        try:
            parts = url.strip().split('/')
            return parts[-1] if parts else f"Camera {default_num}"
        except:
            return f"Camera {default_num}"

    # Determine RTMP source preference
    source_pref = vehicle_info.get("rtmp_source", get_rtmp_source()) if vehicle_info else get_rtmp_source()
    
    def clean_rtmp(url):
        if not url: return ""
        url = url.strip()
        if url in ("ADD_LATER", "add_later", "-", "N/A", "NA"): return ""
        return url

    mongo_rtmp = vehicle_info.get("mongo_rtmp", {}) if vehicle_info else {}
    rtmp_streams = {
        "1": {"name": extract_stream_name(mongo_rtmp.get("rtmp1"), 1), "url": clean_rtmp(mongo_rtmp.get("rtmp1", ""))},
        "2": {"name": extract_stream_name(mongo_rtmp.get("rtmp2"), 2), "url": clean_rtmp(mongo_rtmp.get("rtmp2", ""))},
        "3": {"name": extract_stream_name(mongo_rtmp.get("rtmp3"), 3), "url": clean_rtmp(mongo_rtmp.get("rtmp3", ""))},
        "4": {"name": extract_stream_name(mongo_rtmp.get("rtmp4"), 4), "url": clean_rtmp(mongo_rtmp.get("rtmp4", ""))}
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

    try:
        from mongodb import mongo_client
        _ad = mongo_client["gps_server_db"]["assign_devices"].find_one({"truck_id": {"$regex": f"^{device_id}$", "$options": "i"}}, {"_id": 0, "role": 1})
        device_role = _ad.get("role", "") if _ad else ""
    except Exception:
        device_role = ""
    # If truck user logged in directly, force truck role
    if session.get('user_type') == 'truck':
        device_role = "TRUCK_USER"

    return render_template_string(
        get_template("DEVICE_DASHBOARD_HTML"),
        device_id=device_id,
        initial_data=device_data,
        streams=rtmp_streams,
        logo_url=LOGO_URL,
        gd_phone=gd_phone,
        tr_phone=tr_phone,
        dr_phone=dr_phone,
        device_role=device_role
    )


# --- GPS API Routes ---
@app.route('/api/vehicle/<vehicle_id>')
def api_vehicle(vehicle_id):
    try:
        data = get_live_gps(vehicle_id)
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
        col_vehicles.update_one({"device_id": v_id}, {"$set": {"record_config": config}}, upsert=True)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/api/save_vehicle_display_name', methods=['POST'])
def save_vehicle_display_name():
    try:
        req_data = request.json or {}
        device_id = req_data.get('device_id')
        display_name = req_data.get('display_name')
        col_vehicles.update_one({"device_id": device_id}, {"$set": {"display_name": display_name}}, upsert=True)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/save_global_config', methods=['POST'])
def save_global_config():
    try:
        req_data = request.json or {}
        config = req_data.get('config')
        col_settings.update_one({"_id": "global_gps_config"}, {"$set": {"config": config}}, upsert=True)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/calibrate_vehicle', methods=['POST'])
def calibrate_vehicle():
    try:
        req_data = request.json or {}
        device_id = req_data.get('device_id')
        angle = req_data.get('angle')
        col_vehicles.update_one({"device_id": device_id}, {"$set": {"calibrate_pending": angle}}, upsert=True)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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


@app.route("/stream_player")
def stream_player():
    src = request.args.get("src", "").strip()
    if not src:
        return "<h3>Error: No stream URL provided</h3>", 400
    
    # Render a clean, self-contained HTML player page with HLS.js
    player_template = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Stream Player</title>
    <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
    <style>
        html, body {
            margin: 0;
            padding: 0;
            width: 100%;
            height: 100%;
            background: #000;
            overflow: hidden;
            display: flex;
            align-items: center;
            justify-content: center;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        }
        #video-container {
            position: relative;
            width: 100%;
            height: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        video {
            width: 100%;
            height: 100%;
            object-fit: contain;
        }
        .overlay {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.75);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            color: #fff;
            z-index: 10;
            transition: opacity 0.3s;
        }
        .hidden {
            display: none !important;
        }
        .spinner {
            border: 4px solid rgba(255,255,255,0.1);
            width: 36px;
            height: 36px;
            border-radius: 50%;
            border-left-color: #3b82f6;
            animation: spin 1s linear infinite;
            margin-bottom: 12px;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        .status-title {
            font-weight: 500;
            font-size: 0.95rem;
        }
        .error-text {
            color: #ef4444;
            font-size: 0.85rem;
            margin-top: 8px;
            text-align: center;
            padding: 0 10px;
        }
        .retry-btn {
            margin-top: 12px;
            background: #2563eb;
            color: white;
            border: none;
            padding: 6px 16px;
            border-radius: 4px;
            cursor: pointer;
            font-weight: 500;
            font-size: 0.85rem;
        }
        .retry-btn:hover {
            background: #1d4ed8;
        }
    </style>
</head>
<body>
    <div id="video-container">
        <div id="overlay" class="overlay">
            <div id="spinner" class="spinner"></div>
            <div id="status-text" class="status-title">Connecting...</div>
            <div id="error-text" class="error-text hidden"></div>
            <button id="retry-btn" class="retry-btn hidden" onclick="startStream()">Retry Now</button>
        </div>
        <video id="videoPlayer" autoplay muted playsinline></video>
    </div>

    <script>
        const rtmpUrl = {{ rtmp_url | tojson }};
        let hls = null;
        let streamId = null;
        let heartbeatInterval = null;
        let reconnectTimer = null;
        let isConnecting = false;

        function showStatus(text, showSpinner = true, errorText = "") {
            document.getElementById("overlay").classList.remove("hidden");
            document.getElementById("status-text").innerText = text;
            const spinner = document.getElementById("spinner");
            if (showSpinner) spinner.classList.remove("hidden");
            else spinner.classList.add("hidden");
            
            const errEl = document.getElementById("error-text");
            if (errorText) {
                errEl.innerText = errorText;
                errEl.classList.remove("hidden");
                document.getElementById("retry-btn").classList.remove("hidden");
            } else {
                errEl.classList.add("hidden");
                document.getElementById("retry-btn").classList.add("hidden");
            }
        }

        function hideStatus() {
            document.getElementById("overlay").classList.add("hidden");
        }

        async function startStream() {
            if (isConnecting) return;
            isConnecting = true;
            
            stopStream();
            showStatus("Connecting to camera...", true);
            
            try {
                const res = await fetch(`/play_rtmp?src=${encodeURIComponent(rtmpUrl)}`);
                const data = await res.json();
                
                if (data.error) {
                    throw new Error(data.error);
                }
                
                streamId = data.stream_id;
                playHls(data.hls_url + '?t=' + Date.now());
                
                // Start heartbeat
                startHeartbeat();
            } catch (err) {
                console.error("Stream error:", err);
                showStatus("Offline", false, err.message || "Could not connect to camera stream.");
                scheduleReconnect();
            } finally {
                isConnecting = false;
            }
        }

        function playHls(src) {
            const video = document.getElementById("videoPlayer");
            if (Hls.isSupported()) {
                hls = new Hls({
                    startPosition: -1,
                    liveSyncDurationCount: 3,
                    manifestLoadingMaxRetry: 10,
                    manifestLoadingRetryDelay: 1000,
                });
                hls.loadSource(src);
                hls.attachMedia(video);
                
                hls.on(Hls.Events.MANIFEST_PARSED, () => {
                    video.play().catch(e => console.log("Play blocked, waiting for interaction"));
                    hideStatus();
                });
                
                hls.on(Hls.Events.ERROR, (event, data) => {
                    if (data.fatal) {
                        console.warn("Fatal HLS error:", data.type);
                        stopStream();
                        showStatus("Stream Interrupted", false, "Trying to reconnect...");
                        scheduleReconnect();
                    }
                });
            } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
                video.src = src;
                video.play();
                hideStatus();
            }
        }

        function stopStream() {
            // Clear reconnect timers
            if (reconnectTimer) {
                clearTimeout(reconnectTimer);
                reconnectTimer = null;
            }
            
            // Clear heartbeat
            if (heartbeatInterval) {
                clearInterval(heartbeatInterval);
                heartbeatInterval = null;
            }
            
            // Destroy HLS player
            if (hls) {
                hls.destroy();
                hls = null;
            }
            
            const video = document.getElementById("videoPlayer");
            video.pause();
            video.src = "";
            
            // Tell server to stop stream
            if (streamId) {
                fetch('/stop_stream', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ stream_id: streamId }),
                    keepalive: true
                });
                streamId = null;
            }
        }

        function startHeartbeat() {
            if (heartbeatInterval) clearInterval(heartbeatInterval);
            heartbeatInterval = setInterval(() => {
                if (streamId) {
                    fetch('/keep_alive', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ stream_ids: [streamId] })
                    }).catch(e => {});
                }
            }, 3000);
        }

        function scheduleReconnect() {
            if (reconnectTimer) clearTimeout(reconnectTimer);
            reconnectTimer = setTimeout(() => {
                startStream();
            }, 10000); // Retry every 10 seconds
        }

        // Start playing on page load
        window.addEventListener("load", startStream);
        
        // Clean up on unload
        window.addEventListener("beforeunload", stopStream);
    </script>
</body>
</html>
    """
    return render_template_string(player_template, rtmp_url=src)


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
        
        col_gps_live.update_one({"device_id": device_id}, {"$set": {"mosfet": new_val}}, upsert=True)
        
        # Keep mosfet_states in sync
        from mongodb import mongo_client
        mongo_client["gps_server_db"]["mosfet_states"].update_one(
            {"truck_id": device_id},
            {"$set": {"truck_id": device_id, "state": new_val, "updated_at": datetime.now()}},
            upsert=True
        )
        
        return jsonify({"success": True, "message": f"Camera turned {action} successfully"})
            
    except Exception as e:
        print(f"Error toggling camera: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/hls/<stream_id>/<path:filename>")
def hls(stream_id, filename):
    folder = STREAM_ROOT / stream_id
    if not folder.exists(): abort(404)
    return send_from_directory(folder, filename)

@app.route("/api/stream_log/<stream_id>")
def stream_log(stream_id):
    if session.get('user_type') != 'admin': return "Unauthorized", 403
    log_path = STREAM_ROOT / stream_id / "ffmpeg.log"
    if not log_path.exists(): return "No log yet", 404
    return log_path.read_text(errors="replace")[-3000:]


@app.route("/get_gps_update/<device_id>")
def get_gps_update(device_id):
    return api_vehicle(device_id)
@app.route("/reset_eeprom", methods=["POST"])
def reset_eeprom():
    if session.get('user_type') != 'admin':
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    try:
        data = request.json or {}
        device_id = data.get("device_id")
        password = data.get("password", "")

        # Confirmation password gate before wiping a device's config.
        if password != "reset":
            return jsonify({"success": False, "message": "Incorrect password"}), 403

        if not device_id:
            return jsonify({"success": False, "message": "Device ID missing"}), 400

        # Signal the device to wipe its EEPROM config via MongoDB
        col_gps_live.update_one({"device_id": device_id}, {"$set": {"reset_eeprom": 1}}, upsert=True)
        return jsonify({
            "success": True,
            "message": f"Reset sent to {device_id}. It will reboot into setup (AP) mode shortly. Connect to the 'PILAB-GPS' WiFi to reconfigure."
        })

    except Exception as e:
        print(f"Error sending EEPROM reset: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/check_sos")
def check_sos():
    """Polled by every page. Returns devices currently raising an SOS and logs each event."""
    if 'username' not in session:
        return jsonify({"active": []})

    try:
        active_devices = {doc["device_id"] for doc in col_gps_live.find({"sos": 1}, {"device_id": 1}) if doc.get("device_id")}
    except Exception:
        active_devices = set()

    now = datetime.now()
    active = []

    # Open a log entry for each newly-active device; collect the active list to return.
    for device_id in active_devices:
        open_log = col_sos_logs.find_one({"device_id": device_id, "status": "active"})
        if not open_log:
            col_sos_logs.insert_one({
                "device_id": device_id,
                "started_at": now,
                "ended_at": None,
                "status": "active"
            })
            open_log = col_sos_logs.find_one({"device_id": device_id, "status": "active"})
        started = open_log.get("started_at", now) if open_log else now
        active.append({
            "device_id": device_id,
            "started_at": started.isoformat() if hasattr(started, "isoformat") else str(started)
        })

    # Close logs for devices no longer raising SOS.
    for log in col_sos_logs.find({"status": "active"}):
        if log.get("device_id") not in active_devices:
            col_sos_logs.update_one({"_id": log["_id"]}, {"$set": {"status": "resolved", "ended_at": now}})

    return jsonify({"active": active})


@app.route("/reset_sos", methods=["POST"])
def reset_sos():
    """Called by the overlay the moment it shows (or closes) to clear Firebase sos/<device> back to 0."""
    if 'username' not in session:
        return jsonify({"ok": False}), 403
    device_id = request.json.get("device_id", "") if request.is_json else request.form.get("device_id", "")
    if not device_id:
        return jsonify({"ok": False, "error": "no device_id"}), 400
    try:
        col_gps_live.update_one({"device_id": device_id}, {"$set": {"sos": 0}}, upsert=True)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True})


@app.route("/sos_unread_count")
def sos_unread_count():
    if 'username' not in session:
        return jsonify({"count": 0})
    last_seen = session.get("sos_last_seen")
    if last_seen:
        count = col_sos_logs.count_documents({"started_at": {"$gt": last_seen}})
    else:
        count = col_sos_logs.count_documents({})
    return jsonify({"count": count})


@app.route("/sos_logs")
def sos_logs():
    if 'username' not in session:
        return redirect(url_for('login'))

    # Stamp last-seen so unread count resets after opening this page
    session["sos_last_seen"] = datetime.now()
    session.modified = True

    raw = list(col_sos_logs.find().sort("started_at", -1).limit(500))
    logs = []
    for lg in raw:
        started = lg.get("started_at")
        ended = lg.get("ended_at")
        logs.append({
            "device_id": lg.get("device_id", "?"),
            "started": started.strftime("%d-%b-%Y %I:%M:%S %p") if hasattr(started, "strftime") else str(started or "-"),
            "ended": ended.strftime("%d-%b-%Y %I:%M:%S %p") if hasattr(ended, "strftime") else "-",
            "status": lg.get("status", "")
        })
    return render_template("sos_logs.html", logs=logs, logo_url=LOGO_URL)


@app.route("/download_session_zip/<vehicle_name>/<date>/<int:session_number>")
def download_session_zip(vehicle_name, date, session_number):
    """Download all videos from a session as a ZIP file"""
    if session.get('user_type') != 'admin':
        return redirect(url_for('login'))
    
    try:
        import zipfile
        import io
        from pathlib import Path
        
        # Build the session folder path
        base_path = Path(__file__).parent / "recordings" / vehicle_name / date / str(session_number)
        
        if not base_path.exists():
            return "Session folder not found", 404
        
        # Create in-memory ZIP file
        memory_file = io.BytesIO()
        
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Find all MP4 files in the session folder
            video_files = list(base_path.glob("*.mp4"))
            
            if not video_files:
                return "No video files found in this session", 404
            
            # Add each video to the ZIP
            for video_file in video_files:
                # Add file to zip with just the filename (not full path)
                zf.write(video_file, arcname=video_file.name)
        
        # Seek to beginning of file
        memory_file.seek(0)
        
        # Create filename for the ZIP
        zip_filename = f"{vehicle_name}_{date}_Trip{session_number}.zip"
        
        # Send the ZIP file
        return send_file(
            memory_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name=zip_filename
        )
        
    except Exception as e:
        print(f"Error creating session ZIP: {e}")
        import traceback
        traceback.print_exc()
        return f"Error creating ZIP file: {str(e)}", 500


@app.route("/api/zip/start", methods=["POST"])
def api_zip_start():
    """Start a background ZIP creation job"""
    if session.get('user_type') != 'admin':
        return jsonify({"error": "Unauthorized"}), 403

    try:
        data = request.json
        rel_path = data.get("path", "").strip("/")
        mode = data.get("mode", "local")
        selected_files = data.get("files") # Optional list of specific files
        
        base_path = SSH_BASE_PATH if mode == "remote" else LOCAL_BASE_PATH
        target_dir = Path(base_path) / rel_path
        if not target_dir.exists():
            target_dir = Path(__file__).parent / "recordings" / rel_path

        if not target_dir.exists() or not target_dir.is_dir():
            return jsonify({"error": "Folder not found"}), 404

        # Generate unique Job ID
        job_id = f"zip_{int(time.time())}_{os.urandom(4).hex()}"
        
        # 1. Identify files to zip
        files_to_zip = []
        if selected_files:
            for f in selected_files:
                f_path = target_dir / f
                if f_path.exists() and f_path.is_file():
                    files_to_zip.append(f_path)
        else:
            for f_path in target_dir.rglob("*"):
                if f_path.is_file():
                    files_to_zip.append(f_path)

        if not files_to_zip:
            return jsonify({"error": "No files found to ZIP"}), 404

        ZIP_PROGRESS[job_id] = {
            "current": 0,
            "total": len(files_to_zip),
            "status": "starting",
            "file_path": None,
            "zip_name": f"{os.path.basename(rel_path) or 'recordings'}.zip",
            "timestamp": time.time()
        }

        # 2. Start background thread
        def background_zip(jid, files, base_dir, final_name):
            try:
                temp_file = ZIP_TEMP_DIR / f"{jid}.zip"
                with zipfile.ZipFile(temp_file, 'w', zipfile.ZIP_STORED) as zf:
                    for i, f_path in enumerate(files):
                        arcname = f_path.relative_to(base_dir)
                        zf.write(f_path, arcname=arcname)
                        ZIP_PROGRESS[jid]["current"] = i + 1
                        ZIP_PROGRESS[jid]["status"] = "processing"
                
                ZIP_PROGRESS[jid]["status"] = "completed"
                ZIP_PROGRESS[jid]["file_path"] = str(temp_file)
            except Exception as e:
                print(f"❌ Background ZIP Error [{jid}]: {e}")
                ZIP_PROGRESS[jid]["status"] = "error"
                ZIP_PROGRESS[jid]["error"] = str(e)

        thread = threading.Thread(target=background_zip, args=(job_id, files_to_zip, target_dir, ZIP_PROGRESS[job_id]["zip_name"]))
        thread.daemon = True
        thread.start()

        return jsonify({"success": True, "job_id": job_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/zip/status/<job_id>")
def api_zip_status(job_id):
    """Check status of a ZIP job"""
    status = ZIP_PROGRESS.get(job_id)
    if not status:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(status)

@app.route("/api/zip/download/<job_id>")
def api_zip_download(job_id):
    """Download the completed ZIP file"""
    job = ZIP_PROGRESS.get(job_id)
    if not job or job["status"] != "completed":
        return jsonify({"error": "ZIP not ready or job not found"}), 404
    
    file_path = job["file_path"]
    if not os.path.exists(file_path):
        return jsonify({"error": "Physical file missing"}), 404

    # Send file and cleanup afterwards via a wrapper or simple return
    # For simplicity in this env, we just return it. 
    # In production, you'd use a background cleanup task for old temp files.
    return send_file(file_path, as_attachment=True, download_name=job["zip_name"])


# --- MONTHLY REPORT ROUTES ---
@app.route("/monthly_report")
def monthly_report():
    if session.get('user_type') != 'admin':
        return redirect(url_for('login'))
        
    # Get list of all registered vehicles to populate dropdown (only with non-empty device_id)
    vehicles = list(col_vehicles.find(
        {"device_id": {"$exists": True, "$ne": ""}},
        {"device_id": 1, "rc_number": 1, "driver_name": 1, "_id": 0}
    ))
    
    # Get unique device_ids from map recordings
    recordings_devices = col_map_recordings.distinct("device_id")
    
    # Remove any fallback devices that are already registered (case & space insensitive)
    registered_ids = {v.get("device_id", "").strip().lower().replace(" ", "") for v in vehicles}
    
    fallback_devices = []
    for dev in recordings_devices:
        if dev and dev.strip().lower().replace(" ", "") not in registered_ids:
            fallback_devices.append(dev)
            
    return render_template_string(
        get_template("monthly_report.html"),
        vehicles=vehicles,
        recordings_devices=fallback_devices,
        logo_url=LOGO_URL
    )

@app.route("/api/monthly_report_data")
def api_monthly_report_data():
    if session.get('user_type') != 'admin':
        return jsonify({"error": "Unauthorized"}), 403
        
    device_id = request.args.get('device_id', '').strip()
    date_str = request.args.get('date', '').strip()
    
    if not device_id or not date_str:
        return jsonify({"error": "Missing device_id or date"}), 400
        
    # Parse date
    def parse_date(d_str):
        for fmt in ("%Y-%m-%d", "%d/%m/%y", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(d_str, fmt)
            except ValueError:
                continue
        return None
        
    target_dt = parse_date(date_str)
    if not target_dt:
        return jsonify({"error": f"Invalid date format: {date_str}"}), 400
        
    # Smart Year-Mapping: Check if the user specified a 2025 date,
    # but the database has data in 2026 for the same day and month.
    start_dt = target_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt = start_dt + timedelta(days=1)
    
    def fetch_points(s_dt, e_dt):
        points = list(col_map_recordings.find({
            "device_id": device_id,
            "timestamp": {"$gte": s_dt, "$lt": e_dt}
        }).sort("timestamp", 1))
        return [pt for pt in points if is_valid_gps_coordinate(pt.get('lat'), pt.get('lng'))]
        
    points = fetch_points(start_dt, end_dt)
    
    # Fallback to year 2026 if no data found in 2025
    mapped_year = False
    original_date_str = date_str
    if not points and start_dt.year == 2025:
        alt_start_dt = start_dt.replace(year=2026)
        alt_end_dt = alt_start_dt + timedelta(days=1)
        points = fetch_points(alt_start_dt, alt_end_dt)
        if points:
            start_dt = alt_start_dt
            end_dt = alt_end_dt
            target_dt = alt_start_dt
            date_str = start_dt.strftime("%Y-%m-%d")
            mapped_year = True
            
    # Filter spikes (transient coordinate jumps)
    points = filter_coordinate_spikes(points)
    
    # Calculate vehicle information
    vehicle_info = col_vehicles.find_one({"device_id": device_id}) or {}
    driver_name = vehicle_info.get("driver_name") or "Pending/Unassigned"
    transporter_name = vehicle_info.get("transporter_name") or "Unassigned"
    godown_manager = vehicle_info.get("godown_manager") or "Unassigned"
    rc_number = vehicle_info.get("rc_number") or "N/A"
    
    # Calculate stats
    total_pings = len(points)
    total_distance = 0.0
    max_speed = 0.0
    speeds = []
    moving_speeds = []
    
    def safe_float(val):
        try:
            return float(str(val).replace('"', '').replace("'", '').strip())
        except:
            return 0.0
            
    import math
    def haversine_distance(coord1, coord2):
        R = 6371.0 # km
        lat1, lon1 = math.radians(coord1[0]), math.radians(coord1[1])
        lat2, lon2 = math.radians(coord2[0]), math.radians(coord2[1])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c
        
    for i in range(total_pings):
        pt = points[i]
        speed = safe_float(pt.get('speed', 0))
        # Ignore glitched speeds (> 120 km/h) from stats, but keep valid coordinates
        if speed <= 120.0:
            speeds.append(speed)
            if speed > 2.0:
                moving_speeds.append(speed)
            
        if i > 0:
            prev = points[i-1]
            dist = haversine_distance((prev['lat'], prev['lng']), (pt['lat'], pt['lng']))
            total_distance += dist
            
    max_speed = max(speeds) if speeds else 0.0
    avg_speed = sum(moving_speeds) / len(moving_speeds) if moving_speeds else 0.0
    
    # Enhanced stop detection
    stops = []
    current_stop = None
    SPEED_THRESHOLD = 2.0 # km/h
    MIN_STOP_DURATION = 120 # seconds (2 minutes)
    
    for i, pt in enumerate(points):
        speed = safe_float(pt.get('speed', 0))
        ts = pt['timestamp']
        
        if speed < SPEED_THRESHOLD:
            if current_stop is None:
                current_stop = {
                    'start_time': ts,
                    'end_time': ts,
                    'lat': pt['lat'],
                    'lng': pt['lng'],
                    'points': [pt]
                }
            else:
                # Check for transmission gap between consecutive points inside the stop
                last_pt = current_stop['points'][-1]
                time_diff = (ts - last_pt['timestamp']).total_seconds()
                if time_diff > 120: # > 2 minutes transmission gap
                    # Close current stop if valid, then reset
                    duration = (current_stop['end_time'] - current_stop['start_time']).total_seconds()
                    if duration >= MIN_STOP_DURATION and len(current_stop['points']) >= 5:
                        avg_lat = sum(p['lat'] for p in current_stop['points']) / len(current_stop['points'])
                        avg_lng = sum(p['lng'] for p in current_stop['points']) / len(current_stop['points'])
                        
                        # Apply midnight exclusion filter (12:00 AM to 6:00 AM)
                        if not (0 <= current_stop['start_time'].hour < 6):
                            stops.append({
                                'start_time': current_stop['start_time'].isoformat(),
                                'end_time': current_stop['end_time'].isoformat(),
                                'duration': int(duration),
                                'lat': avg_lat,
                                'lng': avg_lng
                            })
                    current_stop = {
                        'start_time': ts,
                        'end_time': ts,
                        'lat': pt['lat'],
                        'lng': pt['lng'],
                        'points': [pt]
                    }
                else:
                    current_stop['end_time'] = ts
                    current_stop['points'].append(pt)
        else:
            if current_stop:
                duration = (current_stop['end_time'] - current_stop['start_time']).total_seconds()
                if duration >= MIN_STOP_DURATION and len(current_stop['points']) >= 5:
                    avg_lat = sum(p['lat'] for p in current_stop['points']) / len(current_stop['points'])
                    avg_lng = sum(p['lng'] for p in current_stop['points']) / len(current_stop['points'])
                    
                    # Apply midnight exclusion filter (12:00 AM to 6:00 AM)
                    if not (0 <= current_stop['start_time'].hour < 6):
                        stops.append({
                            'start_time': current_stop['start_time'].isoformat(),
                            'end_time': current_stop['end_time'].isoformat(),
                            'duration': int(duration),
                            'lat': avg_lat,
                            'lng': avg_lng
                        })
                current_stop = None
                
    if current_stop:
        duration = (current_stop['end_time'] - current_stop['start_time']).total_seconds()
        if duration >= MIN_STOP_DURATION and len(current_stop['points']) >= 5:
            avg_lat = sum(p['lat'] for p in current_stop['points']) / len(current_stop['points'])
            avg_lng = sum(p['lng'] for p in current_stop['points']) / len(current_stop['points'])
            
            # Apply midnight exclusion filter (12:00 AM to 6:00 AM)
            if not (0 <= current_stop['start_time'].hour < 6):
                stops.append({
                    'start_time': current_stop['start_time'].isoformat(),
                    'end_time': current_stop['end_time'].isoformat(),
                    'duration': int(duration),
                    'lat': avg_lat,
                    'lng': avg_lng
                })
            
    # Merge consecutive stops at the same location with a small time gap (e.g. < 5 minutes)
    merged_stops = []
    if stops:
        # Helper to parse ISO format back to datetime
        def parse_iso(dt_str):
            try:
                return datetime.fromisoformat(dt_str)
            except:
                return datetime.strptime(dt_str[:19], "%Y-%m-%dT%H:%M:%S")

        current = stops[0]
        for next_stop in stops[1:]:
            # Calculate distance between stop locations
            dist = haversine_distance((current['lat'], current['lng']), (next_stop['lat'], next_stop['lng']))
            
            # Calculate time gap between end of current stop and start of next stop
            end_t = parse_iso(current['end_time'])
            start_t = parse_iso(next_stop['start_time'])
            time_gap = (start_t - end_t).total_seconds()
            
            # Merge if distance < 100 meters (0.1 km) AND time gap < 300 seconds (5 minutes)
            if dist < 0.1 and time_gap < 300:
                current['end_time'] = next_stop['end_time']
                dur_sec = (parse_iso(current['end_time']) - parse_iso(current['start_time'])).total_seconds()
                current['duration'] = int(dur_sec)
                current['lat'] = (current['lat'] + next_stop['lat']) / 2.0
                current['lng'] = (current['lng'] + next_stop['lng']) / 2.0
            else:
                merged_stops.append(current)
                current = next_stop
        merged_stops.append(current)
        stops = merged_stops

    # Compile stops risk levels
    total_stop_duration = sum(s['duration'] for s in stops)
    suspicious_stops_count = sum(1 for s in stops if s['duration'] > 900) # > 15 minutes
    
    # Format points for JSON
    json_points = []
    for pt in points:
        json_points.append({
            'lat': pt['lat'],
            'lng': pt['lng'],
            'speed': safe_float(pt.get('speed', 0)),
            'time': pt['timestamp'].strftime("%H:%M:%S"),
            'timestamp': pt['timestamp'].isoformat()
        })
        
    return jsonify({
        "success": True,
        "device_id": device_id,
        "date": date_str,
        "original_date": original_date_str,
        "mapped_year": mapped_year,
        "vehicle_info": {
            "driver_name": driver_name,
            "transporter_name": transporter_name,
            "godown_manager": godown_manager,
            "rc_number": rc_number
        },
        "stats": {
            "total_pings": total_pings,
            "total_distance": round(total_distance, 2),
            "max_speed": round(max_speed, 2),
            "avg_speed": round(avg_speed, 2),
            "stops_count": len(stops),
            "suspicious_stops_count": suspicious_stops_count,
            "total_stop_duration": total_stop_duration
        },
        "stops": stops,
        "points": json_points
    })


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for('login'))


@app.route("/manage_sections")
def manage_sections():
    if session.get('user_type') != 'admin':
        return redirect(url_for('login'))
    return render_template("manage_sections.html")


@app.route("/api/save_sections", methods=["POST"])
def api_save_sections():
    if session.get('user_type') != 'admin':
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    data = request.get_json()
    if not data or "sections" not in data:
        return jsonify({"success": False, "error": "Invalid request data"}), 400
    
    try:
        col_settings.update_one(
            {"_id": "sidebar_sections"},
            {"$set": {"sections": data["sections"]}},
            upsert=True
        )
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/reset_sections", methods=["POST"])
def api_reset_sections():
    if session.get('user_type') != 'admin':
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    try:
        default_sections = [
            {"id": "add_user", "name": "Add New User", "href": "/add_user", "icon": "fas fa-user-plus", "visible": True},
            {"id": "show_devices", "name": "Show Devices", "href": "/admin_dashboard", "icon": "fas fa-list-ul", "visible": True},
            {"id": "gps_monitoring", "name": "GPS Monitoring", "href": "/gps_monitoring", "icon": "fas fa-map-marked-alt", "visible": True},
            {"id": "tracking", "name": "Tracking", "href": "/tracking?v=5", "icon": "fas fa-route", "visible": True},
            {"id": "detailed_report", "name": "Detailed Report", "href": "/monthly_report", "icon": "fas fa-file-invoice", "visible": True},
            {"id": "list_roles", "name": "List Roles", "href": "/list_roles", "icon": "fas fa-users", "visible": True},
            {"id": "grouping", "name": "Grouping", "href": "/grouping", "icon": "fas fa-object-group", "visible": True},
            {"id": "manage_rtmp", "name": "RTMP Link Management", "href": "/manage_rtmp", "icon": "fas fa-link", "visible": True},
            {"id": "recordings", "name": "Recordings", "href": "/recordings", "icon": "fas fa-video", "visible": True},
            {"id": "map_recording", "name": "Map Recording", "href": "/map_recording", "icon": "fas fa-map-marked", "visible": True},
            {"id": "sos_logs", "name": "SOS Logs", "href": "/sos_logs", "icon": "fas fa-triangle-exclamation", "visible": True}
        ]
        col_settings.update_one(
            {"_id": "sidebar_sections"},
            {"$set": {"sections": default_sections}},
            upsert=True
        )
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/cleanup_tests")
def cleanup_tests():
    try:
        from mongodb import mongo_client
        db = mongo_client["gps_server_db"]
        t1 = db["new_devices"].delete_many({"truck_id": {"$regex": "^VERIFY_.*", "$options": "i"}})
        t2 = db["gps_live"].delete_many({"device_id": {"$regex": "^VERIFY_.*", "$options": "i"}})
        t3 = db["map_recordings"].delete_many({"device_id": {"$regex": "^VERIFY_.*", "$options": "i"}})
        return jsonify({
            "success": True,
            "deleted_new_devices": t1.deleted_count,
            "deleted_gps_live": t2.deleted_count,
            "deleted_map_recordings": t3.deleted_count
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def _sos_background_poller():
    """Runs forever in a daemon thread. Polls Firebase sos.json every 5s and records
    events in MongoDB regardless of whether any browser is open."""
    while True:
        try:
            active_devices = {doc["device_id"] for doc in col_gps_live.find({"sos": 1}, {"device_id": 1}) if doc.get("device_id")}
            now = datetime.now()

            for device_id in active_devices:
                if not col_sos_logs.find_one({"device_id": device_id, "status": "active"}):
                    col_sos_logs.insert_one({
                        "device_id": device_id,
                        "started_at": now,
                        "ended_at": None,
                        "status": "active"
                    })

            for log in col_sos_logs.find({"status": "active"}):
                if log.get("device_id") not in active_devices:
                    col_sos_logs.update_one(
                        {"_id": log["_id"]},
                        {"$set": {"status": "resolved", "ended_at": now}}
                    )
        except Exception:
            pass
        time.sleep(5)


_poller_thread = threading.Thread(target=_sos_background_poller, daemon=True)
_poller_thread.start()


def run_autostart_and_db_check():
    log_file = Path(__file__).parent / "boot_diagnostics.log"
    log_entries = []
    log_entries.append(f"Boot at {datetime.now()}")
    
    # 1. Check MongoDB vehicles count
    try:
        from mongodb import col_vehicles
        count = col_vehicles.count_documents({})
        log_entries.append(f"DB vehicles count: {count}")
        # List device IDs
        devices = [doc.get("device_id") for doc in col_vehicles.find({}, {"device_id": 1})]
        log_entries.append(f"Registered Devices: {devices}")
    except Exception as e:
        log_entries.append(f"DB connection error: {e}")
        
    # 2. PM2 autostart
    try:
        flag_file = Path(__file__).parent / ".pm2_initialized"
        if not flag_file.exists():
            log_entries.append("Initializing PM2 auto-start...")
            # Try to start pm2 for sms_curl_acess.py
            cmd = "pm2 start /home/lenovo1/python-server/sms_curl_acess.py --name 'sms-sender' --interpreter /home/lenovo1/python-server/venv/bin/python"
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
            log_entries.append(f"PM2 STDOUT: {res.stdout}")
            log_entries.append(f"PM2 STDERR: {res.stderr}")
            
            # Save
            subprocess.run("pm2 save", shell=True)
            flag_file.write_text("done")
            log_entries.append("PM2 auto-start initialized and saved.")
        else:
            log_entries.append("PM2 auto-start already initialized in past runs.")
    except Exception as e:
        log_entries.append(f"PM2 autostart error: {e}")
        
    # Write log
    with open(log_file, "w", encoding="utf-8") as f:
        f.write("\n".join(log_entries) + "\n\n")


threading.Thread(target=run_autostart_and_db_check, daemon=True).start()



# --- GPS TRACKING INTEGRATION ROUTES ---
def serve_index_no_cache():
    dist_dir = os.path.join(os.path.dirname(__file__), "GPS tracking part 2", "dist")
    resp = make_response(send_from_directory(dist_dir, "index.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

@app.route("/tracking")
@app.route("/tracking/")
def tracking_index():
    if session.get('user_type') not in ('admin', 'truck', 'akhada', 'user'):
        return redirect(url_for('login'))
    return serve_index_no_cache()

@app.route("/tracking/assets/<path:path>")
def tracking_assets(path):
    dist_assets_dir = os.path.join(os.path.dirname(__file__), "GPS tracking part 2", "dist", "assets")
    return send_from_directory(dist_assets_dir, path)

@app.route("/tracking/<path:path>")
def tracking_files(path):
    dist_dir = os.path.join(os.path.dirname(__file__), "GPS tracking part 2", "dist")
    full_path = os.path.join(dist_dir, path)
    if os.path.exists(full_path) and os.path.isfile(full_path):
        return send_from_directory(dist_dir, path)
    return serve_index_no_cache()

@app.route("/api/v1/routing", methods=["GET"])
@app.route("/api/v1/routing/<path:path>", methods=["GET"])
def proxy_routing(path=""):
    url = "https://maps.googleapis.com/maps/api/directions/json"
    params = request.args.to_dict()
    try:
        resp = requests.get(url, params=params, timeout=10)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("content-type"))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/v1/snapping/<path:path>", methods=["GET"])
def proxy_snapping(path):
    url = f"https://roads.googleapis.com/v1/{path}"
    params = request.args.to_dict()
    try:
        resp = requests.get(url, params=params, timeout=10)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("content-type"))
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route("/api/v1/tracking/vehicles", methods=["GET"])
def api_tracking_vehicles():
    if session.get('user_type') not in ('admin', 'truck', 'akhada', 'user'):
        return jsonify({"error": "Unauthorized"}), 403
        
    try:
        # 1. Fetch live telemetry from col_gps_live
        live_docs = list(col_gps_live.find({}))
        vehicles = {}
        for doc in live_docs:
            d_id = doc.get("device_id")
            if not d_id:
                continue
            
            # format timestamp from updated_at (which is in UTC)
            updated_at = doc.get("updated_at")
            if updated_at:
                if hasattr(updated_at, "tzinfo") and updated_at.tzinfo is not None:
                    utc_time = updated_at.astimezone(timezone.utc).replace(tzinfo=None)
                else:
                    offset = datetime.now() - datetime.utcnow()
                    utc_time = updated_at - offset
                timestamp_str = utc_time.strftime("%Y-%m-%d %H:%M:%S")
            else:
                timestamp_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                
            vehicles[d_id] = {
                "lat": float(doc.get("lat")) if doc.get("lat") is not None else None,
                "lng": float(doc.get("lng")) if doc.get("lng") is not None else None,
                "speed": float(doc.get("speed")) if doc.get("speed") is not None else 0.0,
                "timestamp": timestamp_str,
                "hdop": float(doc.get("hdop", 1.0)) if doc.get("hdop") is not None else 1.0,
                "sats": int(doc.get("sats", doc.get("satellites", 8))),
                "is_jammed": bool(doc.get("is_jammed", False)),
                "is_estimated": bool(doc.get("is_estimated", False)),
                "sos": int(doc.get("sos", 0)),
                "mosfet": int(doc.get("mosfet", 0)) if doc.get("mosfet") is not None else 0,
                "truck_id": d_id,
                "vehicle_id": d_id
            }
            
        # 2. Fetch vehicle registry details from col_vehicles
        registry_docs = list(col_vehicles.find({}))
        vehicle_details = {}
        for doc in registry_docs:
            d_id = doc.get("device_id")
            if d_id:
                vehicle_details[d_id] = {
                    "display_name": doc.get("display_name") or d_id,
                    "driver_name": doc.get("driver_name", "N/A"),
                    "driver_phone": doc.get("driver_phone", "N/A"),
                    "transporter_name": doc.get("transporter_name", "N/A"),
                    "rc_number": doc.get("rc_number", "N/A"),
                    "config": doc.get("record_config")
                }
                
        # 3. Fetch global config
        config_doc = col_settings.find_one({"_id": "global_gps_config"})
        global_config = config_doc.get("config") if config_doc else None
                
        return jsonify({
            "vehicles": vehicles,
            "vehicle_details": vehicle_details,
            "global_config": global_config
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/v1/tracking/logs", methods=["GET", "POST", "DELETE"])
def api_tracking_logs():
    if session.get('user_type') not in ('admin', 'truck', 'akhada', 'user'):
        return jsonify({"error": "Unauthorized"}), 403
        
    col_tracking_logs = col_settings.database["tracking_logs"]
    
    if request.method == "GET":
        try:
            logs = {}
            for doc in col_tracking_logs.find({}, {"_id": 0}):
                key = doc.get("key")
                if key:
                    logs[key] = doc.get("log", {})
            return jsonify(logs)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
            
    elif request.method == "POST":
        try:
            data = request.get_json(silent=True) or {}
            key = data.get("key")
            log_data = data.get("log")
            if not key or not log_data:
                return jsonify({"error": "Missing key or log"}), 400
                
            col_tracking_logs.update_one(
                {"key": key},
                {"$set": {"key": key, "log": log_data}},
                upsert=True
            )
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
            
    elif request.method == "DELETE":
        try:
            col_tracking_logs.delete_many({})
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500


@app.route("/api/v1/tracking/sms_queue", methods=["POST"])
def api_tracking_sms_queue():
    if session.get('user_type') not in ('admin', 'truck', 'akhada', 'user'):
        return jsonify({"error": "Unauthorized"}), 403
        
    try:
        data = request.get_json(silent=True) or {}
        col_sms_queue = col_settings.database["sms_queue"]
        
        col_sms_queue.insert_one({
            "to": data.get("to"),
            "message": data.get("message"),
            "status": "pending",
            "lat": data.get("lat"),
            "lng": data.get("lng"),
            "timestamp": datetime.utcnow()
        })
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/v1/tracking/save_recording", methods=["POST"])
def api_save_recording():
    if session.get('user_type') not in ('admin', 'truck', 'akhada', 'user'):
        return jsonify({"error": "Unauthorized"}), 403
        
    try:
        data = request.get_json(silent=True) or {}
        points = data.get("points", [])
        device_id = data.get("vehicleId")
        
        if not device_id or not points:
            return jsonify({"error": "Missing params"}), 400
            
        col_map_recordings = col_settings.database["map_recordings"]
        
        # Insert each point into MongoDB
        for p in points:
            ts_str = p.get("timestamp")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except:
                    ts = datetime.utcnow()
            else:
                ts = datetime.utcnow()
                
            col_map_recordings.insert_one({
                "device_id": device_id,
                "lat": float(p.get("lat")),
                "lng": float(p.get("lng")),
                "speed": float(p.get("speed", 0.0)),
                "timestamp": ts
            })
            
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500




if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7777, debug=True, threaded=True)