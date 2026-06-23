import subprocess
import os
import sys

# --- CONFIGURATION ---
SERVER_USER = "lenovo"
SERVER_HOST = "103.250.160.75"
REMOTE_PATH = "/home/lenovo/6-2-2026-GPS/Latest GPS"  # Path with space
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

    # 1. Create a compressed package
    print("📦 Packing files...")
    exclude_args = " ".join([f'--exclude="{item}"' for item in EXCLUDES])
    run_local(f'tar {exclude_args} -czf {PACKAGE_NAME} .')

    # 2. Upload to server
    print(f"📤 Uploading to {SERVER_HOST}...")
    run_local(f'scp {PACKAGE_NAME} {SERVER_USER}@{SERVER_HOST}:"{REMOTE_PATH}/"')

    # 3. Create a temporary remote script to handle the restart
    print("🔧 Extracting and restarting on server...")
    
    script_content = f"""#!/bin/bash
cd "{REMOTE_PATH}"
tar -xzf {PACKAGE_NAME}
rm {PACKAGE_NAME}
source venv/bin/activate || python3 -m venv venv
./venv/bin/pip install -r requirements.txt
pkill -f "python3 app.py" || true
sleep 2
nohup ./venv/bin/python3 app.py > output.log 2>&1 &
echo "✅ Server started at $(date)"
"""
    
    with open("remote_deploy.sh", "w", newline='\n', encoding='utf-8') as f:
        f.write(script_content)
    
    # Upload the script
    run_local(f'scp remote_deploy.sh {SERVER_USER}@{SERVER_HOST}:"{REMOTE_PATH}/"')
    
    # Run the script
    run_local(f'ssh {SERVER_USER}@{SERVER_HOST} "bash \\"{REMOTE_PATH}/remote_deploy.sh\\" && rm \\"{REMOTE_PATH}/remote_deploy.sh\\""')

    # 4. Cleanup Local
    for f in [PACKAGE_NAME, "remote_deploy.sh"]:
        if os.path.exists(f):
            os.remove(f)

    print("\n✨ DEPLOYMENT COMPLETE! ✨")
    print(f"URL: http://{SERVER_HOST}:7777")

if __name__ == "__main__":
    deploy()
