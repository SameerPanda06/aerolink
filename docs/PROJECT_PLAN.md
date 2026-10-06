# AeroLink project plan

## Goal

Build a reliable remote telemetry and image-transfer system:

```text
Pi sensors + image/ML
        -> SX1278 LoRa
        -> ESP32 ground receiver
        -> telemetry/file interface
        -> backend and dashboard
```

The acceptance goal is real sensor and classification data delivered over the real radio link with validation, ACK/retry behavior, image integrity checks, and visible delivery status. Simulated values must be labelled and must not be used as final evidence.

## Completed: 6 milestones

- [x] Pi and ESP32 SX1278 hardware respond with version `0x12`.
- [x] Both radios use the agreed 433 MHz / BW125 / SF7 / CR4/5 / CRC-on configuration.
- [x] Raw Pi-to-ESP32 RF test: five packets received with approximately `-34 dBm` RSSI and `9.5 dB` SNR.
- [x] AeroLink DATA/ACK transport: five of five packets delivered and acknowledged.
- [x] Structured telemetry payload: 206-byte JSON payload received and acknowledged.
- [x] Reproducible package, serial monitor, tests, and README committed to `main`.

## Current baseline

The transport is working. The last successful test used sample IMU and classification values. Those values are test data, not live sensor or model output.

Do not change `pi/radio.py`, `pi/packet.py`, or the ESP32 radio configuration while adding the next layer unless a test exposes a transport defect.

## Next milestone: live MPU6050 telemetry

### Code changes

- [ ] Add `pi/mpu6050.py` for I2C bus 1, address `0x68`.
- [ ] Wake the sensor through register `0x6B`.
- [ ] Read accelerometer registers `0x3B`, `0x3D`, `0x3F` using scale `16384.0`.
- [ ] Read gyroscope registers `0x43`, `0x45`, `0x47` using scale `131.0`.
- [ ] Add `pi/live_telemetry.py` using the existing DATA/ACK transport.
- [ ] Replace sample IMU values with one real sensor reading per packet.
- [ ] Keep classification explicitly marked as `UNAVAILABLE` until the real model is connected.

### Acceptance test

- [ ] `i2cdetect -y 1` shows `0x68`.
- [ ] Five live packets are sent.
- [ ] All five receive matching ACKs.
- [ ] ESP32 output shows the actual changing sensor values.
- [ ] No packet exceeds the 247-byte application payload limit.

## Following milestones

### Image classification integration

- [ ] Locate the real model, image directory, preprocessing rules, and class labels.
- [ ] Add a small classifier adapter that returns class, confidence, model version, and inference time.
- [ ] Put classification metadata into telemetry first; do not transmit images yet.
- [ ] Test classification metadata with five acknowledged packets.

### Image transfer

- [ ] Define a versioned metadata packet.
- [ ] Define image transfer ID, chunk number, total chunks, chunk size, and checksum.
- [ ] Implement missing-chunk status and selective retransmission.
- [ ] Reassemble and checksum an image on the receiver.
- [ ] Test interruption and resume behavior.

### Ground software

- [ ] Define the Pi-to-ground interface.
- [ ] Add telemetry persistence and link metrics.
- [ ] Add REST/WebSocket APIs.
- [ ] Build the dashboard only against the stable API.

### Final acceptance

- [ ] Real MPU telemetry.
- [ ] Real classification result.
- [ ] Real image transfer and integrity verification.
- [ ] DATA/ACK/retry/duplicate metrics.
- [ ] Backend persistence and API.
- [ ] Frontend live updates and reconnect behavior.
- [ ] End-to-end test with no unlabelled simulation.

## Progress

Six transport/setup milestones are complete. The next required milestone is live MPU telemetry. The remaining work is sensor integration, classification integration, image transfer, ground software, dashboard, and final acceptance.

## Working rule

Change one layer, run its acceptance test, record the evidence, and then continue. Preserve the working RF baseline while higher-level features are added.
