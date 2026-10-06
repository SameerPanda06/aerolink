import time
from radio import SX1278

radio = SX1278()
try:
    radio.initialize()
    for number in range(1, 6):
        payload = f"AEROLINK_RAW_TEST_{number}".encode()
        print(f"[TX] Sending {payload!r}")
        radio.send(payload)
        time.sleep(2)
finally:
    radio.close()
