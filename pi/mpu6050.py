"""Minimal MPU6050 reader for I2C bus 1, address 0x68."""

try:
    from smbus2 import SMBus
except ImportError:
    from smbus import SMBus


class MPU6050:
    ADDRESS = 0x68
    PWR_MGMT_1 = 0x6B
    ACCEL_XOUT_H = 0x3B
    GYRO_XOUT_H = 0x43

    def __init__(self, bus_number=1):
        self.bus = SMBus(bus_number)
        self.bus.write_byte_data(self.ADDRESS, self.PWR_MGMT_1, 0x00)

    def close(self):
        self.bus.close()

    def _signed16(self, high_register):
        data = self.bus.read_i2c_block_data(self.ADDRESS, high_register, 2)
        value = (data[0] << 8) | data[1]
        return value - 65536 if value & 0x8000 else value

    def read(self):
        ax = self._signed16(self.ACCEL_XOUT_H) / 16384.0
        ay = self._signed16(self.ACCEL_XOUT_H + 2) / 16384.0
        az = self._signed16(self.ACCEL_XOUT_H + 4) / 16384.0
        gx = self._signed16(self.GYRO_XOUT_H) / 131.0
        gy = self._signed16(self.GYRO_XOUT_H + 2) / 131.0
        gz = self._signed16(self.GYRO_XOUT_H + 4) / 131.0

        return {
            "accel_x_g": round(ax, 4),
            "accel_y_g": round(ay, 4),
            "accel_z_g": round(az, 4),
            "gyro_x_dps": round(gx, 4),
            "gyro_y_dps": round(gy, 4),
            "gyro_z_dps": round(gz, 4),
        }
