#include <Arduino.h>
#include <SPI.h>
#include <LittleFS.h>
#include "mbedtls/sha256.h"

#define NSS 5
#define RST 14
#define DIO0 26
#define SCK 18
#define MISO 19
#define MOSI 23

// Status LEDs. GPIO2 is the common ESP32 onboard blue LED. Change RED_LED_PIN
// to match the external red LED wiring, or set it to -1 to disable red.
#define BLUE_LED_PIN 2
#define RED_LED_PIN 4
#define LED_ACTIVE_HIGH true

#define FIFO 0x00
#define OP_MODE 0x01
#define FRF_MSB 0x06
#define FRF_MID 0x07
#define FRF_LSB 0x08
#define PA_CONFIG 0x09
#define FIFO_ADDR 0x0D
#define TX_BASE 0x0E
#define RX_BASE 0x0F
#define RX_CURRENT 0x10
#define IRQ 0x12
#define RX_BYTES 0x13
#define SNR 0x19
#define RSSI 0x1A
#define MODEM1 0x1D
#define MODEM2 0x1E
#define PREAMBLE_MSB 0x20
#define PREAMBLE_LSB 0x21
#define MODEM3 0x26
#define SYNC 0x39
#define VERSION_REG 0x42

#define LONG_RANGE 0x80
#define SLEEP_MODE 0x00
#define STANDBY_MODE 0x01
#define TX_MODE 0x03
#define RX_CONTINUOUS 0x05
#define RX_DONE 0x40
#define PHY_CRC_ERROR 0x20
#define TX_DONE 0x08

#define MAGIC 0xA5
#define PROTOCOL_VERSION 0x01
#define TYPE_DATA 0x01
#define TYPE_ACK 0x02
#define HEADER_SIZE 6
#define CRC_SIZE 2
#define IMAGE_MANIFEST 0xC0
#define IMAGE_CHUNK 0xC1
#define IMAGE_MAX_CHUNKS 2048
#define IMAGE_BITMAP_BYTES (IMAGE_MAX_CHUNKS / 8)
#define IMAGE_PATH "/aerolink_image.part"
#define IMAGE_DIR "/images"

uint8_t readReg(uint8_t reg) {
  digitalWrite(NSS, LOW); SPI.transfer(reg & 0x7F);
  uint8_t value = SPI.transfer(0); digitalWrite(NSS, HIGH); return value;
}

void writeReg(uint8_t reg, uint8_t value) {
  digitalWrite(NSS, LOW); SPI.transfer(reg | 0x80); SPI.transfer(value); digitalWrite(NSS, HIGH);
}

uint16_t crc16(const uint8_t *data, uint16_t length) {
  uint16_t crc = 0xFFFF;
  for (uint16_t i = 0; i < length; i++) {
    crc ^= (uint16_t)data[i] << 8;
    for (uint8_t bit = 0; bit < 8; bit++)
      crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
  }
  return crc;
}

bool imageActive = false;
uint32_t imageTransferId = 0;
uint32_t imageSize = 0;
uint16_t imageChunkSize = 0;
uint16_t imageTotalChunks = 0;
uint16_t imageReceivedChunks = 0;
uint8_t imageExpectedHash[32];
uint8_t imageBitmap[IMAGE_BITMAP_BYTES];
File imageFile;
char imageFinalPath[48] = IMAGE_DIR "/unknown.jpg";

void setLed(int pin, bool on) {
  if (pin < 0) return;
  digitalWrite(pin, LED_ACTIVE_HIGH ? (on ? HIGH : LOW) : (on ? LOW : HIGH));
}

void setIdleLeds() {
  setLed(BLUE_LED_PIN, false);
  setLed(RED_LED_PIN, true);
}

void setReceivingLeds() {
  setLed(RED_LED_PIN, false);
  setLed(BLUE_LED_PIN, true);
}

bool imageBitSet(uint16_t index) {
  return (imageBitmap[index / 8] & (uint8_t)(1U << (index % 8))) != 0;
}

void imageSetBit(uint16_t index) {
  imageBitmap[index / 8] |= (uint8_t)(1U << (index % 8));
}

void printHash(const uint8_t *hash) {
  for (uint8_t i = 0; i < 32; i++) Serial.printf("%02x", hash[i]);
}

bool verifyImageHash() {
  if (!imageFile) return false;
  imageFile.flush();
  imageFile.close();
  File readFile = LittleFS.open(IMAGE_PATH, "r");
  if (!readFile) return false;
  mbedtls_sha256_context context;
  mbedtls_sha256_init(&context);
  if (mbedtls_sha256_starts(&context, 0) != 0) {
    mbedtls_sha256_free(&context);
    return false;
  }
  uint8_t buffer[256];
  while (readFile.available()) {
    size_t count = readFile.read(buffer, sizeof(buffer));
    if (count == 0 || mbedtls_sha256_update(&context, buffer, count) != 0) {
      readFile.close();
      mbedtls_sha256_free(&context);
      return false;
    }
  }
  readFile.close();
  uint8_t calculated[32];
  bool ok = mbedtls_sha256_finish(&context, calculated) == 0 &&
            memcmp(calculated, imageExpectedHash, sizeof(calculated)) == 0;
  mbedtls_sha256_free(&context);
  Serial.print("[IMAGE] sha256=");
  printHash(calculated);
  Serial.println();
  return ok;
}

void resetImageState() {
  if (imageFile) imageFile.close();
  imageActive = false;
  imageTransferId = 0;
  imageSize = 0;
  imageChunkSize = 0;
  imageTotalChunks = 0;
  imageReceivedChunks = 0;
  strncpy(imageFinalPath, IMAGE_DIR "/unknown.jpg", sizeof(imageFinalPath));
  imageFinalPath[sizeof(imageFinalPath) - 1] = '\0';
  memset(imageBitmap, 0, sizeof(imageBitmap));
}

bool parseManifest(const uint8_t *payload, uint8_t length) {
  if (length != 45) return false;
  uint32_t transfer = ((uint32_t)payload[1] << 24) | ((uint32_t)payload[2] << 16) |
                      ((uint32_t)payload[3] << 8) | payload[4];
  uint32_t size = ((uint32_t)payload[5] << 24) | ((uint32_t)payload[6] << 16) |
                  ((uint32_t)payload[7] << 8) | payload[8];
  uint16_t chunkSize = ((uint16_t)payload[9] << 8) | payload[10];
  uint16_t total = ((uint16_t)payload[11] << 8) | payload[12];
  if (size == 0 || chunkSize == 0 || chunkSize > 238 || total == 0 || total > IMAGE_MAX_CHUNKS) return false;
  uint16_t expectedTotal = (uint16_t)((size + chunkSize - 1) / chunkSize);
  if (total != expectedTotal) return false;
  resetImageState();
  LittleFS.remove(IMAGE_PATH);
  imageFile = LittleFS.open(IMAGE_PATH, "w");
  if (!imageFile) return false;
  imageTransferId = transfer;
  snprintf(imageFinalPath, sizeof(imageFinalPath), IMAGE_DIR "/%08lX.jpg", (unsigned long)imageTransferId);
  imageSize = size;
  imageChunkSize = chunkSize;
  imageTotalChunks = total;
  memcpy(imageExpectedHash, payload + 13, sizeof(imageExpectedHash));
  memset(imageBitmap, 0, sizeof(imageBitmap));
  imageActive = true;
  Serial.printf("[IMAGE] MANIFEST transfer=%08lX bytes=%lu chunks=%u chunk_size=%u\n",
                (unsigned long)imageTransferId, (unsigned long)imageSize,
                imageTotalChunks, imageChunkSize);
  return true;
}

bool handleImageChunk(const uint8_t *payload, uint8_t length) {
  if (!imageActive || length < 9) return false;
  uint32_t transfer = ((uint32_t)payload[1] << 24) | ((uint32_t)payload[2] << 16) |
                      ((uint32_t)payload[3] << 8) | payload[4];
  uint16_t index = ((uint16_t)payload[5] << 8) | payload[6];
  uint16_t total = ((uint16_t)payload[7] << 8) | payload[8];
  uint16_t bytes = length - 9;
  if (transfer != imageTransferId || total != imageTotalChunks || index >= imageTotalChunks) {
    Serial.println("[IMAGE] REJECTED chunk identity");
    return false;
  }
  uint32_t offset = (uint32_t)index * imageChunkSize;
  uint32_t remaining = imageSize - offset;
  uint16_t expected = remaining < imageChunkSize ? (uint16_t)remaining : imageChunkSize;
  if (bytes != expected) {
    Serial.printf("[IMAGE] REJECTED chunk size got=%u expected=%u\n", bytes, expected);
    return false;
  }
  if (!imageBitSet(index)) {
    if (!imageFile) {
      Serial.println("[IMAGE] REJECTED file unavailable");
      return false;
    }
    if (!imageFile.seek(offset, SeekSet)) {
      Serial.printf("[IMAGE] REJECTED seek offset=%lu\n", (unsigned long)offset);
      return false;
    }
    size_t written = imageFile.write(payload + 9, bytes);
    imageFile.flush();
    if (written != bytes) {
      Serial.printf("[IMAGE] REJECTED write got=%u expected=%u\n", (unsigned)written, bytes);
      return false;
    }
    imageSetBit(index);
    imageReceivedChunks++;
  }
  Serial.printf("[IMAGE] CHUNK %u/%u bytes=%u received=%u/%u\n",
                index + 1, imageTotalChunks, bytes, imageReceivedChunks, imageTotalChunks);
  if (imageReceivedChunks == imageTotalChunks) {
    bool valid = verifyImageHash();
    imageFile.close();
    if (valid) {
      LittleFS.remove(imageFinalPath);
      bool renamed = LittleFS.rename(IMAGE_PATH, imageFinalPath);
      Serial.printf("[IMAGE] %s path=%s\n", renamed ? "COMPLETE sha256_ok=1" : "COMPLETE sha256_ok=1 STORAGE_RENAME_FAILED", renamed ? imageFinalPath : IMAGE_PATH);
    } else {
      Serial.printf("[IMAGE] CHECKSUM_FAILED sha256_ok=0 path=%s\n", IMAGE_PATH);
    }
    imageActive = false;
  }
  return true;
}

void set433MHz() {
  uint32_t frf = (uint32_t)(((uint64_t)433000000 << 19) / 32000000);
  writeReg(FRF_MSB, frf >> 16); writeReg(FRF_MID, frf >> 8); writeReg(FRF_LSB, frf);
}

void startRX() {
  writeReg(OP_MODE, LONG_RANGE | STANDBY_MODE);
  writeReg(RX_BASE, 0x00); writeReg(FIFO_ADDR, 0x00); writeReg(IRQ, 0xFF);
  writeReg(OP_MODE, LONG_RANGE | RX_CONTINUOUS);
}

bool transmit(const uint8_t *data, uint8_t length) {
  writeReg(OP_MODE, LONG_RANGE | STANDBY_MODE);
  writeReg(TX_BASE, 0x80); writeReg(FIFO_ADDR, 0x80);
  for (uint8_t i = 0; i < length; i++) writeReg(FIFO, data[i]);
  writeReg(0x22, length); writeReg(PA_CONFIG, 0x8F); writeReg(IRQ, 0xFF);
  writeReg(OP_MODE, LONG_RANGE | TX_MODE);
  unsigned long deadline = millis() + 2000;
  while (millis() < deadline) {
    if (readReg(IRQ) & TX_DONE) { writeReg(IRQ, TX_DONE); return true; }
    delay(1);
  }
  return false;
}

uint8_t buildAck(uint16_t sequence, uint8_t *out) {
  out[0] = MAGIC; out[1] = PROTOCOL_VERSION; out[2] = TYPE_ACK;
  out[3] = sequence >> 8; out[4] = sequence & 0xFF; out[5] = 0;
  uint16_t crc = crc16(out, 6); out[6] = crc >> 8; out[7] = crc & 0xFF; return 8;
}

void setup() {
  Serial.begin(115200); pinMode(NSS, OUTPUT); digitalWrite(NSS, HIGH);
  pinMode(BLUE_LED_PIN, OUTPUT);
  if (RED_LED_PIN >= 0) pinMode(RED_LED_PIN, OUTPUT);
  setIdleLeds();
  if (!LittleFS.begin(true)) { Serial.println("[FS] LittleFS mount failed"); }
  else if (!LittleFS.exists(IMAGE_DIR) && !LittleFS.mkdir(IMAGE_DIR)) { Serial.println("[FS] image directory create failed"); }
  pinMode(RST, OUTPUT); digitalWrite(RST, HIGH); pinMode(DIO0, INPUT);
  SPI.begin(SCK, MISO, MOSI, NSS); digitalWrite(RST, LOW); delay(100); digitalWrite(RST, HIGH); delay(100);
  Serial.println("AEROLINK CLEAN RECEIVER");
  if (readReg(VERSION_REG) != 0x12) { Serial.println("[ERROR] SX1278 NOT FOUND"); while (true) delay(1000); }
  writeReg(OP_MODE, LONG_RANGE | SLEEP_MODE); delay(20); writeReg(OP_MODE, LONG_RANGE | STANDBY_MODE);
  set433MHz(); writeReg(MODEM1, 0x72); writeReg(MODEM2, 0x74); writeReg(MODEM3, 0x04);
  writeReg(PREAMBLE_MSB, 0); writeReg(PREAMBLE_LSB, 8); writeReg(SYNC, 0x12); writeReg(TX_BASE, 0x80);
  Serial.println("[RADIO] 433MHz SF7 BW125 CR4/5 CRC ON SYNC=0x12"); startRX(); Serial.println("[RX] READY");
}

void loop() {
  uint8_t irq = readReg(IRQ);
  if (irq & PHY_CRC_ERROR) {
    setReceivingLeds();
    Serial.println("[RF] PHY CRC ERROR");
    writeReg(IRQ, 0xFF); startRX(); setIdleLeds(); return;
  }
  if (!(irq & RX_DONE)) { delay(2); return; }
  setReceivingLeds();
  uint8_t length = readReg(RX_BYTES); uint8_t current = readReg(RX_CURRENT); writeReg(FIFO_ADDR, current);
  uint8_t packet[255]; for (uint16_t i = 0; i < length; i++) packet[i] = readReg(FIFO);
  writeReg(IRQ, 0xFF); startRX();
  Serial.printf("[RX] RX_DONE length=%u RSSI=%d SNR=%.2f\n", length, (int)readReg(RSSI) - 157, (int8_t)readReg(SNR) / 4.0f);
  if (length < 8 || packet[0] != MAGIC || packet[1] != PROTOCOL_VERSION) {
    Serial.println("[RX] INVALID HEADER"); setIdleLeds(); return;
  }
  uint8_t payloadLength = packet[5];
  if (length != HEADER_SIZE + payloadLength + CRC_SIZE) {
    Serial.println("[RX] INVALID LENGTH"); setIdleLeds(); return;
  }
  uint16_t received = ((uint16_t)packet[length - 2] << 8) | packet[length - 1];
  uint16_t calculated = crc16(packet, length - 2);
  if (received != calculated) {
    Serial.println("[RX] APPLICATION CRC ERROR"); setIdleLeds(); return;
  }
  uint16_t sequence = ((uint16_t)packet[3] << 8) | packet[4];
  const uint8_t *payload = packet + HEADER_SIZE;
  bool accepted = true;
  if (payloadLength == 45 && payload[0] == IMAGE_MANIFEST) {
    accepted = parseManifest(payload, payloadLength);
  } else if (payloadLength >= 9 && payload[0] == IMAGE_CHUNK) {
    accepted = handleImageChunk(payload, payloadLength);
  }
  if (!accepted) {
    Serial.println("[IMAGE] REJECTED"); setIdleLeds();
    return;
  }
  if (payloadLength >= 9 && payload[0] == IMAGE_CHUNK) {
    uint32_t transfer = ((uint32_t)packet[7] << 24) | ((uint32_t)packet[8] << 16) |
                        ((uint32_t)packet[9] << 8) | packet[10];
    uint16_t chunk = ((uint16_t)packet[11] << 8) | packet[12];
    uint16_t total = ((uint16_t)packet[13] << 8) | packet[14];
    Serial.printf("[RX] IMAGE CHUNK seq=%u chunk=%u/%u bytes=%u transfer=%08lX\n",
                  sequence, chunk + 1, total, payloadLength - 9, (unsigned long)transfer);
  } else if (!(payloadLength == 45 && payload[0] == IMAGE_MANIFEST)) {
    Serial.printf("[RX] VALID DATA seq=%u payload=%u\n", sequence, payloadLength);
  }
  // Allow the Pi SX1278 to finish switching from TX to RX before the
  // downlink ACK is emitted. This is especially important for 217-byte image
  // packets, whose processing path is longer than the small link-test frames.
  uint8_t ack[8]; uint8_t ackLength = buildAck(sequence, ack); delay(200);
  // Repeat the ACK at the protocol layer. If one downlink frame is lost, the
  // Pi can still advance without retransmitting the image data unnecessarily.
  bool ackFirst = transmit(ack, ackLength);
  delay(40);
  bool ackSecond = transmit(ack, ackLength);
  if (ackFirst || ackSecond) {
    Serial.printf("[TX] ACK SENT seq=%u copies=%u\n", sequence, (ackFirst ? 1 : 0) + (ackSecond ? 1 : 0));
  } else {
    Serial.printf("[TX] ACK FAILED seq=%u\n", sequence);
  }
  startRX();
  setIdleLeds();
}
