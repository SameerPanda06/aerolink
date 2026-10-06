import serial
import time
import sys
from datetime import datetime

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM3"
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 115200

try:
    with serial.Serial(PORT, BAUD, timeout=1, write_timeout=1,
                       dsrdtr=False, rtscts=False) as ser:
        time.sleep(0.2)
        print(f"[MONITOR] Connected to {PORT} at {BAUD}")
        with open("esp32_serial.log", "a", buffering=1) as log:
            while True:
                line = ser.readline()
                if not line:
                    continue
                stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                text = line.decode("utf-8", errors="replace").rstrip()
                output = f"[{stamp}] {text}"
                print(output, flush=True)
                log.write(output + "\n")
except KeyboardInterrupt:
    print("[MONITOR] Stopped")
except serial.SerialException as error:
    print(f"[MONITOR] Serial error: {error}")
