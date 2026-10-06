"""SX1278 driver using the proven Pi settings from the legacy project."""

import time
import spidev

try:
    import RPi.GPIO as GPIO
except ImportError:  # Allows syntax/unit checks off the Pi.
    GPIO = None


class SX1278:
    FIFO = 0x00
    OP_MODE = 0x01
    FRF_MSB, FRF_MID, FRF_LSB = 0x06, 0x07, 0x08
    PA_CONFIG, LNA = 0x09, 0x0C
    FIFO_ADDR, TX_BASE, RX_BASE, RX_CURRENT = 0x0D, 0x0E, 0x0F, 0x10
    IRQ, RX_BYTES = 0x12, 0x13
    SNR, RSSI = 0x19, 0x1A
    MODEM1, MODEM2, MODEM3 = 0x1D, 0x1E, 0x26
    PREAMBLE_MSB, PREAMBLE_LSB = 0x20, 0x21
    PAYLOAD_LENGTH, SYNC, VERSION = 0x22, 0x39, 0x42
    LONG_RANGE = 0x80
    SLEEP, STANDBY, TX, RX_CONT = 0x00, 0x01, 0x03, 0x05
    RX_DONE, CRC_ERROR, TX_DONE = 0x40, 0x20, 0x08

    def __init__(self, bus=0, device=0, rst_pin=25, dio0_pin=24):
        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        self.spi.max_speed_hz = 100000
        self.spi.mode = 0
        self.rst_pin, self.dio0_pin = rst_pin, dio0_pin
        if GPIO:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(rst_pin, GPIO.OUT)
            GPIO.setup(dio0_pin, GPIO.IN)

    def close(self):
        if GPIO:
            GPIO.cleanup([self.rst_pin, self.dio0_pin])
        self.spi.close()

    def read(self, reg):
        return self.spi.xfer2([reg & 0x7F, 0])[1]

    def write(self, reg, value):
        self.spi.xfer2([reg | 0x80, value & 0xFF])

    def reset(self):
        if GPIO:
            GPIO.output(self.rst_pin, GPIO.LOW)
            time.sleep(0.1)
            GPIO.output(self.rst_pin, GPIO.HIGH)
            time.sleep(0.1)

    def initialize(self):
        self.reset()
        version = self.read(self.VERSION)
        print(f"[RADIO] VERSION = 0x{version:02X}")
        if version != 0x12:
            raise RuntimeError("SX1278 not detected")
        self.write(self.OP_MODE, self.LONG_RANGE | self.SLEEP)
        time.sleep(0.02)
        self.write(self.OP_MODE, self.LONG_RANGE | self.STANDBY)
        frf = int(433000000 / 61.03515625)
        self.write(self.FRF_MSB, frf >> 16)
        self.write(self.FRF_MID, frf >> 8)
        self.write(self.FRF_LSB, frf)
        self.write(self.PA_CONFIG, 0x8F)
        self.write(self.LNA, 0x23)
        self.write(self.TX_BASE, 0x80)
        self.write(self.RX_BASE, 0x00)
        self.write(self.MODEM1, 0x72)
        self.write(self.MODEM2, 0x74)  # SF7 + PHY CRC ON
        self.write(self.MODEM3, 0x04)
        self.write(self.PREAMBLE_MSB, 0)
        self.write(self.PREAMBLE_LSB, 8)
        self.write(self.SYNC, 0x12)
        self.write(self.IRQ, 0xFF)
        print("[RADIO] Ready: 433 MHz, SF7, BW125, CR4/5, CRC ON")

    def standby(self):
        self.write(self.OP_MODE, self.LONG_RANGE | self.STANDBY)

    def send(self, payload):
        payload = bytes(payload)
        if not 0 < len(payload) <= 255:
            raise ValueError("payload length must be 1..255")
        self.standby()
        self.write(self.FIFO_ADDR, 0x80)
        self.write(self.PAYLOAD_LENGTH, len(payload))
        self.spi.xfer2([self.FIFO | 0x80] + list(payload))
        self.write(self.IRQ, 0xFF)
        self.write(self.OP_MODE, self.LONG_RANGE | self.TX)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if self.read(self.IRQ) & self.TX_DONE:
                self.write(self.IRQ, self.TX_DONE)
                self.standby()
                print(f"[RADIO] TX_DONE ({len(payload)} bytes)")
                return
            time.sleep(0.005)
        raise TimeoutError("SX1278 TX timeout")

    def receive(self, timeout=1.0):
        self.write(self.OP_MODE, self.LONG_RANGE | self.STANDBY)
        self.write(self.FIFO_ADDR, 0)
        self.write(self.IRQ, 0xFF)
        self.write(self.OP_MODE, self.LONG_RANGE | self.RX_CONT)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            irq = self.read(self.IRQ)
            if irq & self.RX_DONE:
                if irq & self.CRC_ERROR:
                    self.write(self.IRQ, 0xFF)
                    return None
                length = self.read(self.RX_BYTES)
                current = self.read(self.RX_CURRENT)
                self.write(self.FIFO_ADDR, current)
                data = bytes(self.read(self.FIFO) for _ in range(length))
                raw_snr = self.read(self.SNR)
                snr = (raw_snr - 256 if raw_snr & 0x80 else raw_snr) / 4
                rssi = self.read(self.RSSI) - 157
                self.write(self.IRQ, 0xFF)
                return data, rssi, snr
            time.sleep(0.005)
        return None
