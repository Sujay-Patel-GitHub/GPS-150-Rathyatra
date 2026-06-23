import subprocess
import sys

SERVER_USER = "lenovo"
SERVER_HOST = "103.250.160.75"

print("🔍 Checking server directory structure...")
print("\nPlease enter your SSH password when prompted.\n")

# Check what directories exist
cmd = f'ssh {SERVER_USER}@{SERVER_HOST} "ls -la /home/lenovo/6-2-2026-GPS/"'
print(f"Running: {cmd}\n")
subprocess.run(cmd, shell=True)

print("\n" + "="*60)
print("Please copy the EXACT directory name from above and update the deployment scripts.")
