"""
Usage:
  python set_mosfet.py TRUCK1 1      # turn ON
  python set_mosfet.py TRUCK1 0      # turn OFF
  python set_mosfet.py TRUCK1        # check current state
"""
import sys
import requests

SERVER = "http://150.129.165.162:7777"

def get_state(truck_id):
    r = requests.get(f"{SERVER}/api/mosfet_state/{truck_id}")
    d = r.json()
    print(f"[{truck_id}] MOSFET state = {'ON' if d['state'] == 1 else 'OFF'} ({d['state']})")

def set_state(truck_id, state):
    r = requests.post(f"{SERVER}/api/mosfet_set/{truck_id}", json={"state": state})
    d = r.json()
    if d.get("ok"):
        print(f"[{truck_id}] MOSFET set to {'ON' if state == 1 else 'OFF'} successfully.")
    else:
        print(f"Error: {d.get('error')}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    truck_id = sys.argv[1]

    if len(sys.argv) == 2:
        get_state(truck_id)
    else:
        state = int(sys.argv[2])
        if state not in (0, 1):
            print("State must be 0 or 1")
            sys.exit(1)
        set_state(truck_id, state)
