# AeroLink merged transport baseline

This package combines the proven legacy Pi SX1278 initialization with the AeroLink DATA/ACK protocol. It intentionally excludes ML and image transfer until the transport is proven.

## Files

- `pi/radio.py`: Pi SX1278 driver (GPIO reset 25, DIO0 24, PA/LNA setup)
- `pi/packet.py`: canonical A5 DATA/ACK packets and CRC16
- `pi/raw_tx.py`: raw RF test
- `pi/reliable_test.py`: five DATA/ACK tests with retries
- `esp32/aerolink_receiver.ino`: clean ESP32 receiver and ACK firmware
- `monitor.py`: plain text ESP32 serial monitor

## Install on Pi

```bash
sudo apt install python3-spidev python3-rpi.gpio
```

Copy `pi/*.py` into one directory on the Pi.

For live MPU6050 telemetry, install the I2C dependency as well:

```bash
sudo apt install python3-smbus i2c-tools
```

## Test order

1. Flash `esp32/aerolink_receiver.ino`.
2. Run `python monitor.py COM3 115200` on the computer connected to the ESP32. If the monitor has fixed `PORT` settings, edit them first.
3. On the Pi, run `python3 pi/raw_tx.py`. Expect `RX_DONE` and raw reception on the ESP32.
4. On the Pi, run `python3 pi/reliable_test.py`. Expect `VALID DATA`, `ACK SENT`, and `5/5 delivered`.

## Live MPU6050 test

After the reliable test passes, verify the sensor on I2C bus 1:

```bash
i2cdetect -y 1
```

Address `0x68` must be present. Then run:

```bash
python3 pi/live_telemetry.py 5
```

The ESP32 should print five valid DATA packets and send five ACKs. Classification is deliberately marked `UNAVAILABLE` until the real model is integrated.

Do not add image or ML payloads until the reliable test passes. The old image protocol is a separate format and must be adapted after this baseline is stable.
