import paramiko
import getpass
import time

SERVER_HOST = "103.250.160.75"
SERVER_USER = "lenovo"
REMOTE_DIR = "/home/lenovo/6-2-2026-GPS/Latest GPS"

def kill_by_pid():
    """Kill specific PIDs and restart"""
    
    password = getpass.getpass(f"Enter SSH password for {SERVER_USER}@{SERVER_HOST}: ")
    
    print("\n🎯 Killing specific PIDs...")
    
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        ssh.connect(SERVER_HOST, username=SERVER_USER, password=password)
        print("✅ Connected!\n")
        
        # Kill the specific PIDs we saw
        pids_to_kill = [228556, 228581]
        
        for pid in pids_to_kill:
            print(f"💀 Killing PID {pid}...")
            stdin, stdout, stderr = ssh.exec_command(f"kill -9 {pid}")
            stdout.channel.recv_exit_status()
        
        print("✅ PIDs killed\n")
        time.sleep(3)
        
        # Check for any remaining processes on port 7777
        print("🔍 Checking for remaining processes...")
        stdin, stdout, stderr = ssh.exec_command("lsof -i :7777 | grep LISTEN")
        remaining = stdout.read().decode().strip()
        
        if remaining:
            print("⚠️  Still found processes:")
            print(remaining)
            
            # Extract PIDs and kill them
            for line in remaining.split('\n'):
                parts = line.split()
                if len(parts) > 1:
                    pid = parts[1]
                    print(f"💀 Killing PID {pid}...")
                    stdin, stdout, stderr = ssh.exec_command(f"kill -9 {pid}")
                    stdout.channel.recv_exit_status()
            
            time.sleep(2)
        
        # Final verification
        stdin, stdout, stderr = ssh.exec_command("lsof -i :7777")
        final_check = stdout.read().decode().strip()
        
        if final_check:
            print("\n❌ Port still occupied:")
            print(final_check)
            print("\n🔧 Trying one more time with sudo...")
            stdin, stdout, stderr = ssh.exec_command("sudo fuser -k 7777/tcp")
            time.sleep(2)
        else:
            print("✅ Port 7777 is FREE!\n")
        
        # Start server
        print("🚀 Starting server...")
        start_cmd = f"cd '{REMOTE_DIR}' && nohup ./venv/bin/python3 app.py > output.log 2>&1 &"
        stdin, stdout, stderr = ssh.exec_command(start_cmd)
        
        # Wait and verify
        print("⏳ Waiting 5 seconds...")
        time.sleep(5)
        
        print("🔍 Verifying server...")
        stdin, stdout, stderr = ssh.exec_command("ps aux | grep 'python3 app.py' | grep -v grep")
        processes = stdout.read().decode().strip()
        
        if processes:
            print("\n✅ ✅ ✅ SERVER IS RUNNING! ✅ ✅ ✅\n")
            print(processes)
            pid = processes.split()[1]
            print(f"\n📌 PID: {pid}")
            print(f"🌐 URL: http://{SERVER_HOST}:7777")
            print("\n🎉 DEPLOYMENT COMPLETE!")
            print("\n📦 Deployed Features:")
            print("  ✅ MongoDB camera fix - Video recordings now fetch from correct source")
            print("  ✅ Download All button - Download entire session as ZIP")
        else:
            print("\n❌ Server not running. Checking logs...")
            stdin, stdout, stderr = ssh.exec_command(f"cd '{REMOTE_DIR}' && tail -25 output.log")
            logs = stdout.read().decode()
            print("\n" + "=" * 60)
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
    print("🎯 KILL BY PID & RESTART")
    print("=" * 60)
    kill_by_pid()
