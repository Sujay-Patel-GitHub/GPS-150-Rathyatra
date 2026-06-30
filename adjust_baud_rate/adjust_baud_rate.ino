#include <Arduino.h>
#include <HardwareSerial.h>

#define GPS_RX   16
#define GPS_TX   17
#define GPS_BAUD 9600

HardwareSerial gpsSerial(2);

void sendUBX(const uint8_t* payload, uint8_t len) {
  gpsSerial.write(0xB5);
  gpsSerial.write(0x62);
  uint8_t ck_a = 0, ck_b = 0;
  for (uint8_t i = 0; i < len; i++) {
    gpsSerial.write(payload[i]);
    ck_a += payload[i];
    ck_b += ck_a;
  }
  gpsSerial.write(ck_a);
  gpsSerial.write(ck_b);
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=============================================");
  Serial.println("U-blox Neo-6M GPS Baud Rate Scanner & Configurator");
  Serial.println("=============================================");

  const uint32_t bauds[] = {9600, 115200, 4800, 19200, 38400, 57600};
  const int numBauds = sizeof(bauds) / sizeof(bauds[0]);
  
  uint32_t detectedBaud = 0;
  
  for (int i = 0; i < numBauds; i++) {
    uint32_t b = bauds[i];
    Serial.printf("[GPS Scan] Testing %u baud...\n", b);
    gpsSerial.begin(b, SERIAL_8N1, GPS_RX, GPS_TX);
    delay(100);
    
    // Clear buffer
    while(gpsSerial.available()) gpsSerial.read();
    
    int dollarCount = 0;
    uint32_t startTime = millis();
    while (millis() - startTime < 1500) {
      while (gpsSerial.available()) {
        char c = gpsSerial.read();
        if (c == '$') {
          dollarCount++;
        }
      }
      delay(5);
    }
    
    if (dollarCount >= 2) {
      detectedBaud = b;
      Serial.printf("[GPS Scan] SUCCESS: Detected GPS at %u baud! (dollarCount=%d)\n", b, dollarCount);
      break;
    }
    
    gpsSerial.end();
  }
  
  if (detectedBaud == 0) {
    Serial.println("[GPS Scan] ERROR: No GPS module detected on pins 16 & 17.");
    Serial.println("Please check your wiring (TX->RX, RX->TX, VCC, GND).");
    Serial.println("Defaulting serial port to 9600 baud for passthrough...");
    gpsSerial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX, GPS_TX);
    return;
  }
  
  if (detectedBaud != GPS_BAUD) {
    Serial.printf("[GPS Config] Changing GPS baud rate from %u to %u...\n", detectedBaud, GPS_BAUD);
    
    // Send UBX-CFG-PRT payload to set UART1 to 9600 baud (8N1)
    uint8_t cfg_prt[] = {
      0x06, 0x00,             // Class: CFG, ID: PRT
      0x14, 0x00,             // Length: 20 bytes
      0x01,                   // Port ID: 1 (UART1)
      0x00,                   // Reserved
      0x00, 0x00,             // TX Ready
      0xC0, 0x08, 0x00, 0x00, // Mode: 8N1 (0x000008C0)
      0x80, 0x25, 0x00, 0x00, // Baud: 9600 (0x00002580)
      0x03, 0x00,             // Input Protocols: UBX + NMEA
      0x03, 0x00,             // Output Protocols: UBX + NMEA
      0x00, 0x00,             // Flags
      0x00, 0x00              // Reserved
    };
    sendUBX(cfg_prt, sizeof(cfg_prt));
    delay(200);
    
    // Re-initialize ESP32 serial port at 9600 baud
    gpsSerial.end();
    gpsSerial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX, GPS_TX);
    delay(200);
    
    // Send UBX-CFG-CFG payload to save configuration to Flash/EEPROM/BBR
    uint8_t cfg_cfg[] = {
      0x06, 0x09,             // Class: CFG, ID: CFG
      0x0D, 0x00,             // Length: 13 bytes
      0x00, 0x00, 0x00, 0x00, // Clear mask
      0xFF, 0xFF, 0x00, 0x00, // Save mask
      0x00, 0x00, 0x00, 0x00, // Load mask
      0x07                    // Device mask: BBR + Flash + EEPROM
    };
    sendUBX(cfg_cfg, sizeof(cfg_cfg));
    delay(500);
    
    Serial.println("[GPS Config] SUCCESS: GPS baud rate permanently set to 9600 and saved.");
  } else {
    Serial.println("[GPS Config] GPS is already configured at 9600 baud. No changes needed.");
  }
  
  Serial.println("\n[Passthrough] Entering GPS Serial Passthrough Mode...");
  Serial.println("You should see NMEA sentences ($GPRMC, $GPGGA, etc.) below:\n");
}

void loop() {
  // Pass data from GPS to USB Serial Monitor
  while (gpsSerial.available()) {
    Serial.write(gpsSerial.read());
  }
  
  // Pass data from USB Serial Monitor to GPS
  while (Serial.available()) {
    gpsSerial.write(Serial.read());
  }
}
