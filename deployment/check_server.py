import paramiko
import getpass

# Server configuration
SERVER_HOST = "103.250.160.75"
SERVER_USER = "lenovo"
REMOTE_DIR = "/home/lenovo/6-2-2026-GPS/Latest GPS"

def check_server():
    """Check server status and view logs"""
    
    password = getpass.getpass(f"Enter SSH password for {SERVER_USER}@{SERVER_HOST}: ")
    
    print("\n🔍 Checking server status...")
    
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        ssh.connect(SERVER_HOST, username=SERVER_USER, password=password)
        print("✅ Connected!\n")
        
        # Check if process is running
        print("📊 Checking for running Python processes...")
        stdin, stdout, stderr = ssh.exec_command("ps aux | grep 'python3 app.py' | grep -v grep")
        processes = stdout.read().decode().strip()
        
        if processes:
            print("✅ Server is running!")
            print(f"\n{processes}\n")
        else:
            print("❌ Server is NOT running\n")
        
        # Check last 30 lines of output.log
        print("📄 Last 30 lines of output.log:")
        print("=" * 60)
        stdin, stdout, stderr = ssh.exec_command(f"cd '{REMOTE_DIR}' && tail -30 output.log")
        log_output = stdout.read().decode()
        error_output = stderr.read().decode()
        
        if log_output:
            print(log_output)
        if error_output:
            print("STDERR:", error_output)
        
        print("=" * 60)
        
        # Try to start if not running
        if not processes:
            print("\n🔄 Attempting to start server...")
            start_cmd = f"cd '{REMOTE_DIR}' && nohup ./venv/bin/python3 app.py > output.log 2>&1 &"
            stdin, stdout, stderr = ssh.exec_command(start_cmd)
            print("✅ Start command sent. Wait a few seconds and run this script again to verify.")
            
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        ssh.close()

if __name__ == "__main__":
    print("=" * 60)
    print("🔍 SERVER STATUS CHECK")
    print("=" * 60)
    check_server()
