#include <WiFi.h>
#include <Wire.h>
#include <TinyGPSPlus.h>
#include <HardwareSerial.h>
#include <HTTPClient.h>

// ── CONFIG — edit these 3 lines only ──────────────────────
const char* TRUCK_NAME  = "TRUCK01";
const char* WIFI_SSID   = "PANIND";
const char* WIFI_PASS   = "12AB89YZ";
// ──────────────────────────────────────────────────────────

const char* SERVER_HOST = "http://150.129.165.162:7777";

// ── GPS
#define GPS_RX   16
#define GPS_TX   17
#define GPS_BAUD 9600
TinyGPSPlus    gps;
HardwareSerial gpsSerial(2);

// ── MPU-6050
#define MPU_ADDR  0x68
#define ACC_SCALE (1.0f / 16384.0f)
#define GYR_SCALE (1.0f / 131.0f)

// ── Shared state
SemaphoreHandle_t dataMutex;
struct SharedData {
  float lat, lng, speed_kmph;
  bool  gps_valid;
  float ax, ay, az;
  float gx, gy, gz;
};
static SharedData shared = {};

// ── Core 0: GPS
void TaskGPS(void* pv) {
  for (;;) {
    while (gpsSerial.available()) {
      if (gps.encode(gpsSerial.read()) && gps.location.isUpdated()) {
        if (xSemaphoreTake(dataMutex, pdMS_TO_TICKS(5)) == pdTRUE) {
          shared.lat        = (float)gps.location.lat();
          shared.lng        = (float)gps.location.lng();
          shared.speed_kmph = gps.speed.isValid() ? (float)gps.speed.kmph() : 0.0f;
          shared.gps_valid  = true;
          xSemaphoreGive(dataMutex);
        }
      }
    }
    vTaskDelay(pdMS_TO_TICKS(5));
  }
}

// ── Core 1: MPU + push every 5s
void TaskMPU(void* pv) {
  static uint32_t lastPushMs = 0;

  for (;;) {
    Wire.beginTransmission(MPU_ADDR);
    Wire.write(0x3B);
    Wire.endTransmission(false);
    Wire.requestFrom(MPU_ADDR, 14, true);
    int16_t raw[7];
    for (int i = 0; i < 7; i++)
      raw[i] = (int16_t)((Wire.read() << 8) | Wire.read());

    float ax_ = raw[0] * ACC_SCALE;
    float ay_ = raw[1] * ACC_SCALE;
    float az_ = raw[2] * ACC_SCALE;
    // raw[3] = temperature, skipped
    float gx_ = raw[4] * GYR_SCALE;
    float gy_ = raw[5] * GYR_SCALE;
    float gz_ = raw[6] * GYR_SCALE;

    if (xSemaphoreTake(dataMutex, pdMS_TO_TICKS(5)) == pdTRUE) {
      shared.ax = ax_; shared.ay = ay_; shared.az = az_;
      shared.gx = gx_; shared.gy = gy_; shared.gz = gz_;
      xSemaphoreGive(dataMutex);
    }

    SharedData snap;
    xSemaphoreTake(dataMutex, portMAX_DELAY);
    snap = shared;
    xSemaphoreGive(dataMutex);

    Serial.printf("[GPS] %s Lat:%.6f Lng:%.6f Spd:%.1f km/h\n",
      snap.gps_valid ? "OK" : "--", snap.lat, snap.lng, snap.speed_kmph);
    Serial.printf("[ACC] X:%.3fg Y:%.3fg Z:%.3fg  [GYR] X:%.1f Y:%.1f Z:%.1f deg/s\n",
      snap.ax, snap.ay, snap.az, snap.gx, snap.gy, snap.gz);

    uint32_t now = millis();
    if ((now - lastPushMs) >= 5000 && WiFi.status() == WL_CONNECTED) {
      lastPushMs = now;
      char body[320];
      snprintf(body, sizeof(body),
        "{\"lat\":%.6f,\"lng\":%.6f,\"speed\":%.2f,"
        "\"ax\":%.4f,\"ay\":%.4f,\"az\":%.4f,"
        "\"gx\":%.3f,\"gy\":%.3f,\"gz\":%.3f}",
        snap.lat, snap.lng, snap.speed_kmph,
        snap.ax, snap.ay, snap.az,
        snap.gx, snap.gy, snap.gz);
      HTTPClient http;
      http.begin(String(SERVER_HOST) + "/api/truck_gps/" + TRUCK_NAME);
      http.addHeader("Content-Type", "application/json");
      int code = http.POST(body);
      Serial.printf("[PUSH] HTTP %d | %s\n\n", code, TRUCK_NAME);
      http.end();
    }

    vTaskDelay(pdMS_TO_TICKS(100));
  }
}

void setup() {
  Serial.begin(115200);
  gpsSerial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX, GPS_TX);

  Wire.begin();
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x6B); Wire.write(0x00);
  Wire.endTransmission(true);
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x1C); Wire.write(0x00);
  Wire.endTransmission(true);
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x1B); Wire.write(0x00);
  Wire.endTransmission(true);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.printf("Connecting [%s] as %s ", WIFI_SSID, TRUCK_NAME);
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 30) {
    delay(500); Serial.print("."); tries++;
  }
  Serial.printf("\n%s\n", WiFi.status() == WL_CONNECTED ?
    WiFi.localIP().toString().c_str() : "WiFi failed -- will retry each push");

  dataMutex = xSemaphoreCreateMutex();
  xTaskCreatePinnedToCore(TaskGPS, "GPS", 8192, NULL, 2, NULL, 0);
  xTaskCreatePinnedToCore(TaskMPU, "MPU", 8192, NULL, 1, NULL, 1);
}

void loop() { vTaskDelay(portMAX_DELAY); }