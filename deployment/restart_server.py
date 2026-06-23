import paramiko
import time
import getpass

# Server configuration
SERVER_HOST = "103.250.160.75"
SERVER_USER = "lenovo"
REMOTE_DIR = "/home/lenovo/6-2-2026-GPS/Latest GPS"

def restart_server():
    """Restart the Python server via SSH"""
    
    # Get password from user
    password = getpass.getpass(f"Enter SSH password for {SERVER_USER}@{SERVER_HOST}: ")
    
    print("\n🔄 Connecting to server...")
    
    # Create SSH client
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        # Connect
        ssh.connect(SERVER_HOST, username=SERVER_USER, password=password)
        print("✅ Connected successfully!")
        
        # Execute restart commands
        print("\n🛑 Stopping old process...")
        stdin, stdout, stderr = ssh.exec_command("pkill -f 'python3 app.py' || true")
        stdout.channel.recv_exit_status()  # Wait for command to complete
        
        print("⏳ Waiting 2 seconds...")
        time.sleep(2)
        
        print("🚀 Starting new process...")
        restart_cmd = f"cd '{REMOTE_DIR}' && nohup ./venv/bin/python3 app.py > output.log 2>&1 &"
        stdin, stdout, stderr = ssh.exec_command(restart_cmd)
        
        # Give it a moment to start
        time.sleep(2)
        
        # Check if process is running
        print("🔍 Verifying server is running...")
        stdin, stdout, stderr = ssh.exec_command("pgrep -f 'python3 app.py'")
        pid = stdout.read().decode().strip()
        
        if pid:
            print(f"✅ Server restarted successfully! (PID: {pid})")
            print(f"\n🌐 Your server is now live at: http://{SERVER_HOST}:7777")
            print("\n📝 The MongoDB camera fix is now active!")
            print("\nTest it by:")
            print("1. Go to RTMP Management and select MongoDB for a vehicle")
            print("2. Go to Video Recordings Archive")
            print("3. Select that vehicle - cameras should show MongoDB links")
        else:
            print("⚠️  Could not verify if server started. Check output.log on server.")
            
    except paramiko.AuthenticationException:
        print("❌ Authentication failed. Please check your password.")
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        ssh.close()
        print("\n🔌 Connection closed.")

if __name__ == "__main__":
    print("=" * 60)
    print("🚀 SERVER RESTART UTILITY")
    print("=" * 60)
    restart_server()
