# AeroLink Pi hardware files

These files belong on the Raspberry Pi that is connected to the SX1278 and,
for live telemetry, the MPU6050. The repository checkout on the Pi should be:

```text
/home/sameer/aerolink/aerolink-main/
```

The hardware scripts are therefore located at:

```text
/home/sameer/aerolink/aerolink-main/hardware/pi/
```

Run the commands below from that directory unless a command shows an absolute
path.

## Files

| File | Purpose |
| --- | --- |
| `packet.py` | AeroLink DATA/ACK packet format and CRC16 |
| `radio.py` | SX1278 driver for 433 MHz, SF7, BW125, CR4/5 |
| `raw_tx.py` | Basic one-way radio smoke test |
| `reliable_test.py` | Five DATA packets with ACK and retry validation |
| `mpu6050.py` | MPU6050 I2C sensor reader |
| `live_telemetry.py` | Sends live MPU6050 telemetry with ACKs |
| `image_transfer_test.py` | Sends a prepared JPEG in acknowledged chunks |
| `mission_send.py` | Runs classification, JPEG preparation, metadata, and image transfer |

The classifier and mission preparation scripts are currently maintained in the
Neuronex Pi project. Their expected locations are:

```text
/home/sameer/aerolink/aerolink-main/pi/classifier.py
/home/sameer/aerolink/aerolink-main/pi/mission_decision.py
/home/sameer/aerolink/aerolink-main/pi/image_prepare.py
/home/sameer/aerolink/aerolink-main/pi/classification_send_test.py
```

The model and mission images remain in the Neuronex project:

```text
/home/sameer/neuronex/models/neuronex.tflite
/home/sameer/neuronex/missions/
```

## Install Pi dependencies

```bash
sudo apt update
sudo apt install -y python3-spidev python3-rpi.gpio python3-smbus i2c-tools
```

Confirm the radio and sensor wiring before running tests. For the MPU6050,
check that address `0x68` is visible:

```bash
i2cdetect -y 1
```

## Test sequence

Start with the ESP32 receiver and serial monitor. Then run these on the Pi:

```bash
cd /home/sameer/aerolink/aerolink-main/hardware/pi

# Radio smoke test
python3 raw_tx.py

# DATA/ACK and retry test
python3 reliable_test.py

# Five live MPU6050 telemetry packets
python3 live_telemetry.py 5
```

For a prepared image, create the JPEG with the Neuronex preparation script and
then send it:

```bash
python3 /home/sameer/aerolink/aerolink-main/pi/image_prepare.py \
  /home/sameer/neuronex/missions/NEX-000001_IMG-000004.jpg \
  /tmp/IMG-000004_q60.jpg \
  60

python3 image_transfer_test.py IMG-000004 /tmp/IMG-000004_q60.jpg

# Complete one-image mission (classifier.py must exist in the documented Pi path)
python3 mission_send.py \
  /home/sameer/neuronex/missions/NEX-000001_IMG-000004.jpg \
  --image-id IMG-000004
```

The current image test validates that every chunk is acknowledged. ESP32 image
reassembly and SHA-256 validation are the next hardware milestone.

## Important paths and roles

- AeroLink transport code: `/home/sameer/aerolink/aerolink-main/hardware/pi/`
- Neuronex model: `/home/sameer/neuronex/models/neuronex.tflite`
- Mission input images: `/home/sameer/neuronex/missions/`
- Temporary prepared JPEGs: `/tmp/`
- ESP32 source and monitor: `/home/sameer/aerolink/aerolink-main/hardware/esp32/`

Do not run two Pi radio programs at the same time; both programs need exclusive
access to SPI bus 0, chip-select 0, and the SX1278.
