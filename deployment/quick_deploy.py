import subprocess
import sys

# --- CONFIGURATION ---
SERVER_USER = "lenovo"
SERVER_HOST = "103.250.160.75"
REMOTE_DIR = "/home/lenovo/6-2-2026-GPS/Latest GPS"  # Path with space

def run_cmd(cmd):
    """Run command and show output"""
    print(f"Running: {cmd}")
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        print(f"❌ Error during: {cmd}")
        sys.exit(1)

def quick_deploy():
    print("🚀 Quick Deploy - Uploading app.py only...")
    
    # 1. Upload app.py - use quotes around the entire path
    print("📤 Uploading app.py...")
    run_cmd(f'scp app.py {SERVER_USER}@{SERVER_HOST}:"{REMOTE_DIR}/"')
    
    # 2. Restart server
    print("🔄 Restarting server...")
    
    # Build command carefully to avoid shell syntax issues
    restart_cmd = (
        f'ssh {SERVER_USER}@{SERVER_HOST} '
        f'"cd \\"{REMOTE_DIR}\\" && '
        f"pkill -f \\'python3 app.py\\' || true && "
        f"sleep 2 && "
        f"nohup ./venv/bin/python3 app.py > output.log 2>&1 & "
        f"echo \\'✅ Server restarted!\\'"
        f'"'
    )
    run_cmd(restart_cmd)
    
    print("\n✨ DEPLOYMENT COMPLETE! ✨")
    print(f"URL: http://{SERVER_HOST}:7777")
    print("\nThe MongoDB camera fix is now live!")

if __name__ == "__main__":
    quick_deploy()
