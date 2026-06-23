import paramiko
import getpass
import time

SERVER_HOST = "103.250.160.75"
SERVER_USER = "lenovo"
REMOTE_DIR = "/home/lenovo/6-2-2026-GPS/Latest GPS"

def super_force_restart():
    """Super aggressive restart"""
    
    password = getpass.getpass(f"Enter SSH password for {SERVER_USER}@{SERVER_HOST}: ")
    
    print("\n💪 SUPER FORCE RESTART...")
    
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        ssh.connect(SERVER_HOST, username=SERVER_USER, password=password)
        print("✅ Connected!\n")
        
        # Multiple attempts to kill
        for attempt in range(3):
            print(f"🔄 Attempt {attempt + 1}/3 to free port 7777...")
            
            # Kill by port
            stdin, stdout, stderr = ssh.exec_command("fuser -k 7777/tcp")
            stdout.channel.recv_exit_status()
            
            # Kill all python3
            stdin, stdout, stderr = ssh.exec_command("pkill -9 python3")
            stdout.channel.recv_exit_status()
            
            # Kill by name
            stdin, stdout, stderr = ssh.exec_command("pkill -9 -f 'app.py'")
            stdout.channel.recv_exit_status()
            
            time.sleep(2)
            
            # Check if port is free
            stdin, stdout, stderr = ssh.exec_command("lsof -i :7777")
            port_check = stdout.read().decode().strip()
            
            if not port_check:
                print("✅ Port 7777 is now free!\n")
                break
            else:
                print(f"⚠️  Port still occupied, retrying...\n")
                time.sleep(2)
        
        # Final check
        stdin, stdout, stderr = ssh.exec_command("lsof -i :7777")
        final_check = stdout.read().decode().strip()
        
        if final_check:
            print("❌ Could not free port 7777. Manual intervention needed.")
            print(final_check)
            return
        
        # Start server
        print("🚀 Starting server...")
        start_cmd = f"cd '{REMOTE_DIR}' && nohup ./venv/bin/python3 app.py > output.log 2>&1 &"
        stdin, stdout, stderr = ssh.exec_command(start_cmd)
        
        # Wait and verify multiple times
        for i in range(3):
            time.sleep(3)
            print(f"🔍 Checking... ({i+1}/3)")
            
            stdin, stdout, stderr = ssh.exec_command("ps aux | grep 'python3 app.py' | grep -v grep")
            processes = stdout.read().decode().strip()
            
            if processes:
                print("\n✅ SERVER IS RUNNING!")
                print(f"\n{processes}\n")
                pid = processes.split()[1]
                print(f"📌 PID: {pid}")
                print(f"🌐 URL: http://{SERVER_HOST}:7777")
                print("\n✨ DEPLOYMENT SUCCESSFUL!")
                print("\n📝 New Features:")
                print("  ✅ MongoDB camera links in video recordings")
                print("  ✅ Download All button for session recordings")
                return
        
        print("\n❌ Server didn't start. Checking logs...")
        stdin, stdout, stderr = ssh.exec_command(f"cd '{REMOTE_DIR}' && tail -30 output.log")
        logs = stdout.read().decode()
        print("\n" + "=" * 60)
        print(logs)
        print("=" * 60)
            
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        ssh.close()

if __name__ == "__main__":
    print("=" * 60)
    print("💪 SUPER FORCE RESTART")
    print("=" * 60)
    super_force_restart()
