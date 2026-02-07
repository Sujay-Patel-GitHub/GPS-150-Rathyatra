import subprocess
import os
import sys

# --- CONFIGURATION ---
SERVER_USER = "lenovo"
SERVER_HOST = "103.250.160.75"
REMOTE_PATH = "/home/lenovo/6-2-2026-GPS/Latest\\ GPS" # Escaped space for shell
PACKAGE_NAME = "deploy_package.tar.gz"

# Files and folders to EXCLUDE from upload (speeds up deployment)
EXCLUDES = [
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "streams",
    "*.pyc",
    ".vscode",
    "deploy_package.tar.gz",
    "deploy.py"
]

def run_local(cmd):
    """Runs a command on your Windows machine."""
    print(f"DEBUG: Running local -> {cmd}")
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        print(f"❌ Error during: {cmd}")
        sys.exit(1)

def deploy():
    print("--- 🚀 STARTING DEPLOYMENT ---")

    # 1. Create a compressed package (using tar which is built into Windows 10/11)
    print("📦 Packing files...")
    exclude_args = " ".join([f'--exclude="{item}"' for item in EXCLUDES])
    run_local(f'tar {exclude_args} -czf {PACKAGE_NAME} .')

    # 2. Upload to server
    print(f"📤 Uploading to {SERVER_HOST}...")
    run_local(f'scp {PACKAGE_NAME} {SERVER_USER}@{SERVER_HOST}:"{REMOTE_PATH}/"')

    # 3. Remote Commands
    print("🔧 Extracting and restarting on server...")
    
    # These commands run ON THE SERVER
    remote_cmds = [
        f"cd {REMOTE_PATH}",
        f"tar -xzf {PACKAGE_NAME}",    # Unpack
        f"rm {PACKAGE_NAME}",         # Cleanup zip
        "source venv/bin/activate || python3 -m venv venv", # Ensure venv exists
        "./venv/bin/pip install -r requirements.txt",      # Install updates
        "pkill -f 'python3 app.py' || true",               # Kill old instance
        "nohup ./venv/bin/python3 app.py > output.log 2>&1 &", # Run in background
        "echo '✅ Server restarted successfully!'"
    ]
    
    remote_cmd_string = " && ".join(remote_cmds)
    run_local(f'ssh {SERVER_USER}@{SERVER_HOST} "{remote_cmd_string}"')

    # 4. Cleanup Local
    if os.path.exists(PACKAGE_NAME):
        os.remove(PACKAGE_NAME)

    print("\n✨ DEPLOYMENT COMPLETE! ✨")
    print(f"URL: http://{SERVER_HOST}:5000 (check your app.py for port)")

if __name__ == "__main__":
    deploy()
