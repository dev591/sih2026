"""
CAN Transport Bridge (Stretch Goal)
Publishes PRAMANA telemetry as CAN frames on vcan0.
Uses CANaerospace / DroneCAN paradigms for data encoding.
"""

import time
import struct
import math

try:
    import can
    CAN_AVAILABLE = True
except ImportError:
    CAN_AVAILABLE = False

def main():
    print("PRAMANA CAN Transport Bridge Starting...")
    if not CAN_AVAILABLE:
        print("Error: python-can not installed. Please run: pip install python-can")
        print("Ensure you have set up vcan0:")
        print("  sudo modprobe vcan")
        print("  sudo ip link add dev vcan0 type vcan")
        print("  sudo ip link set up vcan0")
        return

    try:
        bus = can.interface.Bus(channel='vcan0', bustype='socketcan')
        print("Connected to vcan0.")
    except Exception as e:
        print(f"Failed to connect to vcan0: {e}")
        print("Did you set up the vcan0 interface?")
        return

    # Simulate 1Hz telemetry publishing loop
    print("Publishing telemetry on vcan0. Run 'candump vcan0' to monitor.")
    
    seq = 0
    try:
        while True:
            # Simulated telemetry values for the CAN frame
            rpm = 3800 + int(20 * math.sin(seq / 5.0))
            map_hPa = 1100 + int(5 * math.cos(seq / 5.0))
            cht_C = 120
            
            # Pack using CANaerospace / DroneCAN general principles
            # Format: RPM (uint16), MAP (uint16), CHT (uint16)
            data = struct.pack(">HHHxx", rpm, map_hPa, cht_C)
            
            msg = can.Message(
                arbitration_id=0x300, 
                data=data, 
                is_extended_id=False
            )
            
            bus.send(msg)
            print(f"[{seq}] Sent CAN frame ID=0x300: RPM={rpm}, MAP={map_hPa}, CHT={cht_C}")
            
            seq += 1
            time.sleep(1.0)
            
    except KeyboardInterrupt:
        print("CAN bridge stopped by user.")
    finally:
        bus.shutdown()

if __name__ == "__main__":
    main()
