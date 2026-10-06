import time
from packet import TYPE_ACK, data, parse, PacketError
from radio import SX1278

ACK_TIMEOUT = 1.0
MAX_ATTEMPTS = 3


def send_reliable(radio, sequence, payload):
    packet = data(sequence, payload)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"[LINK] DATA seq={sequence}, attempt {attempt}/{MAX_ATTEMPTS}")
        radio.send(packet)
        result = radio.receive(ACK_TIMEOUT)
        if result is None:
            print("[LINK] ACK TIMEOUT")
            continue
        raw, rssi, snr = result
        try:
            response = parse(raw)
        except PacketError as error:
            print(f"[LINK] Invalid ACK: {error}")
            continue
        if response["type"] == TYPE_ACK and response["sequence"] == sequence:
            print(f"[LINK] ACK RECEIVED seq={sequence} RSSI={rssi} SNR={snr:.2f}")
            return True
        print("[LINK] Unexpected packet")
    return False


radio = SX1278()
try:
    radio.initialize()
    success = 0
    for sequence in range(1, 6):
        if send_reliable(radio, sequence, f"AEROLINK HELLO {sequence}".encode()):
            success += 1
        time.sleep(0.5)
    print(f"[TEST] RESULT: {success}/5 delivered")
finally:
    radio.close()
