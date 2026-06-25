#include <Wire.h>
#include <TinyGPSPlus.h>
#include <HardwareSerial.h>
#include <WiFi.h>
#include <HTTPClient.h>

// --- WiFi Credentials ---
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASS = "YOUR_WIFI_PASSWORD";

// --- Server ---
const char* SERVER_URL = "http://150.129.165.162:7777/api/test_gps";

// --- Pin & Baud Config ---
constexpr int     RX_PIN   = 16;
constexpr int     TX_PIN   = 17;
constexpr uint32_t GPS_BAUD = 9600;

// --- MPU-6050 ---
constexpr int   MPU_ADDR  = 0x68;
constexpr float ACC_SCALE = 1.0f / 16384.0f;
constexpr float GYR_SCALE = 1.0f / 131.0f;

// --- GPS ---
TinyGPSPlus    gps;
HardwareSerial gpsSerial(2);

// --- Shared GPS State ---
struct GpsSnapshot {
  float    lat;
  float    lng;
  float    speed_kmph;
  bool     valid;
  uint32_t updates;
};

static SemaphoreHandle_t gpsMutex;
static GpsSnapshot       shared = {0.0f, 0.0f, 0.0f, false, 0};

void TaskGPS(void *pv);
void TaskMPU(void *pv);

// ============================================================
void setup() {
  Serial.begin(115200);
  gpsSerial.begin(GPS_BAUD, SERIAL_8N1, RX_PIN, TX_PIN);

  Wire.begin();
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x6B);
  Wire.write(0x00);
  Wire.endTransmission(true);

  gpsMutex = xSemaphoreCreateMutex();
  configASSERT(gpsMutex);

  // Connect WiFi
  Serial.printf("Connecting to %s", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nWiFi connected — IP: %s\n", WiFi.localIP().toString().c_str());

  xTaskCreatePinnedToCore(TaskGPS, "GPS", 8192, NULL, 2, NULL, 0);
  xTaskCreatePinnedToCore(TaskMPU, "MPU", 8192, NULL, 1, NULL, 1);
}

void loop() {
  vTaskDelay(portMAX_DELAY);
}

// ============================================================
// CORE 0 — GPS feed
// ============================================================
void TaskGPS(void *pv) {
  for (;;) {
    while (gpsSerial.available()) {
      if (gps.encode(gpsSerial.read()) && gps.location.isUpdated()) {
        if (xSemaphoreTake(gpsMutex, pdMS_TO_TICKS(5)) == pdTRUE) {
          shared.lat        = gps.location.lat();
          shared.lng        = gps.location.lng();
          shared.speed_kmph = gps.speed.isValid() ? gps.speed.kmph() : 0.0f;
          shared.valid      = true;
          shared.updates++;
          xSemaphoreGive(gpsMutex);
        }
      }
    }
    vTaskDelay(pdMS_TO_TICKS(5));
  }
}

// ============================================================
// CORE 1 — MPU read + Serial print + MongoDB push
// ============================================================
void TaskMPU(void *pv) {
  static uint32_t mpuCount   = 0;
  static uint32_t lastPushMs = 0;

  for (;;) {
    mpuCount++;

    // --- Read MPU-6050 ---
    Wire.beginTransmission(MPU_ADDR);
    Wire.write(0x3B);
    Wire.endTransmission(false);
    Wire.requestFrom(MPU_ADDR, 14, true);

    int16_t raw[7];
    for (int i = 0; i < 7; i++)
      raw[i] = (Wire.read() << 8) | Wire.read();

    const float ax   = raw[0] * ACC_SCALE;
    const float ay   = raw[1] * ACC_SCALE;
    const float az   = raw[2] * ACC_SCALE;
    const float temp = raw[3] / 340.0f + 36.53f;
    const float gx   = raw[4] * GYR_SCALE;
    const float gy   = raw[5] * GYR_SCALE;
    const float gz   = raw[6] * GYR_SCALE;

    // --- Snapshot GPS ---
    GpsSnapshot snap;
    xSemaphoreTake(gpsMutex, portMAX_DELAY);
    snap = shared;
    xSemaphoreGive(gpsMutex);

    // --- Serial print ---
    Serial.printf(
      "[MPU:%lu] Ac:%.2f,%.2f,%.2f | Gy:%.1f,%.1f,%.1f | Tmp:%.1fC\t||\t",
      mpuCount, ax, ay, az, gx, gy, gz, temp
    );
    if (snap.valid)
      Serial.printf("[GPS:%lu] Lat:%.6f Lng:%.6f Spd:%.1fkm/h\n",
        snap.updates, snap.lat, snap.lng, snap.speed_kmph);
    else
      Serial.println("[GPS] Searching...");

    // --- Push to MongoDB every 2 seconds if GPS valid ---
    uint32_t now = millis();
    if (snap.valid && WiFi.status() == WL_CONNECTED && (now - lastPushMs) >= 2000) {
      lastPushMs = now;

      HTTPClient http;
      http.begin(SERVER_URL);
      http.addHeader("Content-Type", "application/json");

      char body[80];
      snprintf(body, sizeof(body), "{\"lat\":%.6f,\"lng\":%.6f}", snap.lat, snap.lng);

      int code = http.POST(body);
      if (code == 200)
        Serial.printf("  -> Pushed to MongoDB ✓ (GPS update #%lu)\n", snap.updates);
      else
        Serial.printf("  -> Push failed: HTTP %d\n", code);

      http.end();
    }

    vTaskDelay(pdMS_TO_TICKS(100));
  }
}
