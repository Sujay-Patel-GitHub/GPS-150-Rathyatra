import paramiko
import getpass
import time

# Server configuration
SERVER_HOST = "103.250.160.75"
SERVER_USER = "lenovo"
REMOTE_DIR = "/home/lenovo/6-2-2026-GPS/Latest GPS"

def force_restart():
    """Force kill all python processes and restart"""
    
    password = getpass.getpass(f"Enter SSH password for {SERVER_USER}@{SERVER_HOST}: ")
    
    print("\n🔧 Force restarting server...")
    
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        ssh.connect(SERVER_HOST, username=SERVER_USER, password=password)
        print("✅ Connected!\n")
        
        # Kill ALL python3 processes
        print("💀 Killing all Python processes...")
        stdin, stdout, stderr = ssh.exec_command("pkill -9 python3")
        stdout.channel.recv_exit_status()
        print("✅ All Python processes killed\n")
        
        # Wait a bit
        print("⏳ Waiting 3 seconds...")
        time.sleep(3)
        
        # Check if port is free
        print("🔍 Checking if port 7777 is free...")
        stdin, stdout, stderr = ssh.exec_command("lsof -i :7777")
        port_check = stdout.read().decode().strip()
        
        if port_check:
            print(f"⚠️  Port still in use:\n{port_check}\n")
            print("🔨 Killing process on port 7777...")
            stdin, stdout, stderr = ssh.exec_command("fuser -k 7777/tcp")
            stdout.channel.recv_exit_status()
            time.sleep(2)
        else:
            print("✅ Port 7777 is free\n")
        
        # Start server
        print("🚀 Starting server...")
        start_cmd = f"cd '{REMOTE_DIR}' && nohup ./venv/bin/python3 app.py > output.log 2>&1 &"
        stdin, stdout, stderr = ssh.exec_command(start_cmd)
        
        # Wait for startup
        print("⏳ Waiting 5 seconds for server to start...")
        time.sleep(5)
        
        # Verify
        print("🔍 Verifying server is running...")
        stdin, stdout, stderr = ssh.exec_command("ps aux | grep 'python3 app.py' | grep -v grep")
        processes = stdout.read().decode().strip()
        
        if processes:
            print("✅ Server is running!")
            print(f"\n{processes}\n")
            
            # Extract PID
            pid = processes.split()[1]
            print(f"📌 Process ID: {pid}")
            print(f"🌐 Server URL: http://{SERVER_HOST}:7777")
            print("\n✨ Deployment successful!")
            print("\n📝 Changes deployed:")
            print("  1. MongoDB camera fix for video recordings")
            print("  2. Download All button for session recordings")
        else:
            print("❌ Server failed to start. Checking logs...\n")
            stdin, stdout, stderr = ssh.exec_command(f"cd '{REMOTE_DIR}' && tail -20 output.log")
            logs = stdout.read().decode()
            print("Last 20 lines of output.log:")
            print("=" * 60)
            print(logs)
            print("=" * 60)
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        ssh.close()

if __name__ == "__main__":
    print("=" * 60)
    print("🔨 FORCE RESTART SERVER")
    print("=" * 60)
    force_restart()
