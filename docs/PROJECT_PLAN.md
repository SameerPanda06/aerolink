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

## Completed: live MPU6050 telemetry

### Code changes

- [x] Add `pi/mpu6050.py` for I2C bus 1, address `0x68`.
- [x] Wake the sensor through register `0x6B`.
- [x] Read accelerometer and gyroscope values with the documented scales.
- [x] Add `pi/live_telemetry.py` using the existing DATA/ACK transport.
- [x] Send real sensor values in five packets with five matching ACKs.
- [x] Keep classification explicitly marked as `UNAVAILABLE` until the real model is connected.

### Acceptance test

- [x] `i2cdetect -y 1` shows `0x68`.
- [x] Five live packets are sent.
- [x] All five receive matching ACKs.
- [x] ESP32 output shows changing sensor values.
- [x] No packet exceeds the 247-byte application payload limit.

## Next milestone: image-classification metadata

Before implementing this phase, provide the actual `.tflite` model, labels, preprocessing rules, and one known input/output example. The classifier must be validated locally before its result is sent over LoRa.

- [ ] Locate the model and labels on the Pi.
- [ ] Confirm input shape, colour order, normalization, and output mapping.
- [ ] Add `pi/classifier.py` with a deterministic single-image API.
- [ ] Run local inference and record class, confidence, and latency.
- [ ] Add real classification metadata to telemetry while keeping IMU data.
- [ ] Send and acknowledge five classification telemetry packets.

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

Six transport/setup milestones and the MPU6050 telemetry milestone are complete. The next required milestone is image-classification metadata. The remaining work is classifier integration, image transfer, ground software, dashboard, and final acceptance.

## Working rule

Change one layer, run its acceptance test, record the evidence, and then continue. Preserve the working RF baseline while higher-level features are added.
