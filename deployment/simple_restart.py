import paramiko
import getpass
import time

SERVER_HOST = "103.250.160.75"
SERVER_USER = "lenovo"
REMOTE_DIR = "/home/lenovo/6-2-2026-GPS/Latest GPS"

def simple_kill_and_start():
    """Simple: Kill PIDs, wait, start server"""
    
    password = getpass.getpass(f"Enter SSH password for {SERVER_USER}@{SERVER_HOST}: ")
    
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        ssh.connect(SERVER_HOST, username=SERVER_USER, password=password)
        print("✅ Connected\n")
        
        # Step 1: Find and kill ALL python3 processes
        print("Step 1: Finding all python3 processes...")
        stdin, stdout, stderr = ssh.exec_command("ps aux | grep 'python3' | grep -v grep")
        processes = stdout.read().decode()
        print(processes)
        
        print("\nStep 2: Killing ALL python3 processes...")
        stdin, stdout, stderr = ssh.exec_command("pkill -9 python3")
        stdout.channel.recv_exit_status()
        print("✅ Killed\n")
        
        print("Step 3: Waiting 5 seconds...")
        time.sleep(5)
        
        print("Step 4: Verifying port 7777 is free...")
        stdin, stdout, stderr = ssh.exec_command("lsof -i :7777")
        port_status = stdout.read().decode().strip()
        
        if port_status:
            print("❌ Port still occupied:")
            print(port_status)
            return
        else:
            print("✅ Port is free!\n")
        
        print("Step 5: Starting server...")
        start_cmd = f"cd '{REMOTE_DIR}' && nohup ./venv/bin/python3 app.py > output.log 2>&1 &"
        stdin, stdout, stderr = ssh.exec_command(start_cmd)
        print("✅ Start command sent\n")
        
        print("Step 6: Waiting 8 seconds for startup...")
        time.sleep(8)
        
        print("Step 7: Checking if server is running...")
        stdin, stdout, stderr = ssh.exec_command("ps aux | grep 'python3 app.py' | grep -v grep")
        running = stdout.read().decode().strip()
        
        if running:
            print("\n🎉 SUCCESS! Server is running:")
            print(running)
            pid = running.split()[1]
            print(f"\n✅ PID: {pid}")
            print(f"✅ URL: http://{SERVER_HOST}:7777")
        else:
            print("\n❌ Server not running. Last 20 lines of log:")
            stdin, stdout, stderr = ssh.exec_command(f"cd '{REMOTE_DIR}' && tail -20 output.log")
            print(stdout.read().decode())
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        ssh.close()

if __name__ == "__main__":
    print("=" * 60)
    print("SIMPLE KILL & START")
    print("=" * 60 + "\n")
    simple_kill_and_start()
