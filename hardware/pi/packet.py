"""Canonical AeroLink DATA/ACK packet format."""

MAGIC = 0xA5
VERSION = 0x01
TYPE_DATA = 0x01
TYPE_ACK = 0x02
HEADER_SIZE = 6
CRC_SIZE = 2
MAX_PAYLOAD = 247


class PacketError(ValueError):
    pass


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = (((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)) & 0xFFFF
    return crc


def build(packet_type: int, sequence: int, payload: bytes = b"") -> bytes:
    payload = bytes(payload)
    if not 0 <= sequence <= 0xFFFF:
        raise ValueError("sequence must be 0..65535")
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload exceeds 247 bytes")
    header = bytes([MAGIC, VERSION, packet_type, sequence >> 8, sequence & 0xFF, len(payload)])
    body = header + payload
    crc = crc16_ccitt(body)
    return body + bytes([crc >> 8, crc & 0xFF])


def data(sequence: int, payload: bytes) -> bytes:
    return build(TYPE_DATA, sequence, payload)


def ack(sequence: int) -> bytes:
    return build(TYPE_ACK, sequence)


def parse(raw: bytes) -> dict:
    raw = bytes(raw)
    if len(raw) < HEADER_SIZE + CRC_SIZE:
        raise PacketError("packet too short")
    if raw[0] != MAGIC:
        raise PacketError(f"bad magic 0x{raw[0]:02X}")
    if raw[1] != VERSION:
        raise PacketError(f"unsupported version {raw[1]}")
    length = raw[5]
    expected = HEADER_SIZE + length + CRC_SIZE
    if len(raw) != expected:
        raise PacketError(f"length mismatch: expected {expected}, got {len(raw)}")
    received = (raw[-2] << 8) | raw[-1]
    calculated = crc16_ccitt(raw[:-2])
    if received != calculated:
        raise PacketError(f"CRC mismatch: received 0x{received:04X}, calculated 0x{calculated:04X}")
    return {"type": raw[2], "sequence": (raw[3] << 8) | raw[4], "payload": raw[6:-2]}
