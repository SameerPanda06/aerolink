#include <Arduino.h>
#include <SPI.h>

#define NSS 5
#define RST 14
#define DIO0 26
#define SCK 18
#define MISO 19
#define MOSI 23

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
  if (irq & PHY_CRC_ERROR) { Serial.println("[RF] PHY CRC ERROR"); writeReg(IRQ, 0xFF); startRX(); return; }
  if (!(irq & RX_DONE)) { delay(2); return; }
  uint8_t length = readReg(RX_BYTES); uint8_t current = readReg(RX_CURRENT); writeReg(FIFO_ADDR, current);
  uint8_t packet[255]; for (uint16_t i = 0; i < length; i++) packet[i] = readReg(FIFO);
  writeReg(IRQ, 0xFF); startRX();
  Serial.printf("[RX] RX_DONE length=%u RSSI=%d SNR=%.2f\n", length, (int)readReg(RSSI) - 157, (int8_t)readReg(SNR) / 4.0f);
  if (length < 8 || packet[0] != MAGIC || packet[1] != PROTOCOL_VERSION) { Serial.println("[RX] INVALID HEADER"); return; }
  uint8_t payloadLength = packet[5];
  if (length != HEADER_SIZE + payloadLength + CRC_SIZE) { Serial.println("[RX] INVALID LENGTH"); return; }
  uint16_t received = ((uint16_t)packet[length - 2] << 8) | packet[length - 1];
  uint16_t calculated = crc16(packet, length - 2);
  if (received != calculated) { Serial.println("[RX] APPLICATION CRC ERROR"); return; }
  uint16_t sequence = ((uint16_t)packet[3] << 8) | packet[4];
  if (payloadLength >= 9 && packet[6] == 0xC1) {
    uint32_t transfer = ((uint32_t)packet[7] << 24) | ((uint32_t)packet[8] << 16) |
                        ((uint32_t)packet[9] << 8) | packet[10];
    uint16_t chunk = ((uint16_t)packet[11] << 8) | packet[12];
    uint16_t total = ((uint16_t)packet[13] << 8) | packet[14];
    Serial.printf("[RX] IMAGE CHUNK seq=%u chunk=%u/%u bytes=%u transfer=%08lX\n",
                  sequence, chunk + 1, total, payloadLength - 9, (unsigned long)transfer);
  } else {
    Serial.printf("[RX] VALID DATA seq=%u payload=%u\n", sequence, payloadLength);
  }
  uint8_t ack[8]; uint8_t ackLength = buildAck(sequence, ack); delay(50);
  if (transmit(ack, ackLength)) Serial.printf("[TX] ACK SENT seq=%u\n", sequence);
  else Serial.printf("[TX] ACK FAILED seq=%u\n", sequence);
  startRX();
}
