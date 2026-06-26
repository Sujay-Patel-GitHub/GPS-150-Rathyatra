#include <Arduino.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <NetworkUdp.h>
#include <ArduinoOTA.h>
#include <WebServer.h>
#include <Update.h>
#include <Wire.h>
#include <TinyGPSPlus.h>
#include <HardwareSerial.h>
#include <HTTPClient.h>

// ── CONFIG — edit these lines only ────────────────────────
const char* TRUCK_NAME = "TRUCK1";
const char* WIFI_SSID  = "surya1";
const char* WIFI_PASS  = "Pilab@909090";
#define MOSFET_PIN 27
// ──────────────────────────────────────────────────────────

const char* SERVER_HOST = "http://150.129.165.162:7777";

WebServer otaServer(80);

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

// ── Motion thresholds (truck-tuned, 4 states)
// still  = parked engine-off   avg delta < 0.06g for 8s
// idle   = engine on, stopped  avg delta 0.06-0.35g, speed < 2.5
// moving = driving             speed > 2.5 OR avg delta > 0.35g
// rough  = jolt/pothole        instant delta > 1.5g, holds 3s
#define STILL_THRESHOLD  0.06f
#define IDLE_THRESHOLD   0.35f
#define ROUGH_THRESHOLD  1.5f
#define GPS_MOVING_KMH   2.5f
#define STILL_CONFIRM_MS 8000
#define ROUGH_HOLD_MS    3000
#define DELTA_SAMPLES    8

// ── Shared state
SemaphoreHandle_t dataMutex;
struct SharedData {
  float lat, lng, speed_kmph;
  bool  gps_valid;
  float ax, ay, az;
  float gx, gy, gz;
};
static SharedData shared = {};
uint32_t last_ota_time = 0;

// ── Web OTA page
void handleOTAPage() {
  otaServer.send(200, "text/html",
    "<html><head><title>OTA - " + String(TRUCK_NAME) + "</title>"
    "<style>"
    "body{font-family:sans-serif;background:#0f172a;color:#e2e8f0;"
    "display:flex;align-items:center;justify-content:center;height:100vh;margin:0}"
    ".card{background:#1e293b;padding:32px;border-radius:16px;width:360px;text-align:center}"
    "h2{color:#38bdf8;margin-bottom:6px}"
    "p{color:#64748b;font-size:.85rem;margin-bottom:20px}"
    "input[type=file]{width:100%;padding:10px;background:#0f172a;border:1px dashed #334155;"
    "border-radius:8px;color:#94a3b8;margin-bottom:16px;cursor:pointer;box-sizing:border-box}"
    "button{width:100%;padding:12px;background:#0ea5e9;color:#fff;border:none;"
    "border-radius:8px;font-size:1rem;font-weight:700;cursor:pointer}"
    "button:hover{background:#0284c7}"
    "</style></head><body>"
    "<div class='card'>"
    "<h2>&#128230; OTA Update</h2>"
    "<p>Truck: <strong style='color:#38bdf8'>" + String(TRUCK_NAME) + "</strong><br>"
    "IP: <strong>" + WiFi.localIP().toString() + "</strong></p>"
    "<form method='POST' action='/do_update' enctype='multipart/form-data'>"
    "<input type='file' name='firmware' accept='.bin' required>"
    "<button type='submit'>&#8593; Upload Firmware</button>"
    "</form>"
    "</div></body></html>"
  );
}

void handleOTAUpload() {
  HTTPUpload& upload = otaServer.upload();
  if (upload.status == UPLOAD_FILE_START) {
    Serial.printf("[OTA] Start: %s\n", upload.filename.c_str());
    Update.begin(UPDATE_SIZE_UNKNOWN);
  } else if (upload.status == UPLOAD_FILE_WRITE) {
    Update.write(upload.buf, upload.currentSize);
    Serial.printf("[OTA] Written: %u bytes\n", upload.totalSize);
  } else if (upload.status == UPLOAD_FILE_END) {
    Update.end(true);
    Serial.printf("[OTA] Done: %u bytes\n", upload.totalSize);
  }
}

void handleOTAResult() {
  if (Update.hasError()) {
    otaServer.send(500, "text/html",
      "<html><body style='font-family:sans-serif;background:#0f172a;color:#f87171;"
      "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
      "<div style='text-align:center'><h2>&#10060; Update Failed</h2>"
      "<a href='/update' style='color:#38bdf8'>Try again</a></div></body></html>");
  } else {
    otaServer.send(200, "text/html",
      "<html><body style='font-family:sans-serif;background:#0f172a;color:#22c55e;"
      "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
      "<div style='text-align:center'><h2>&#10003; Success! Rebooting...</h2></div></body></html>");
    delay(2000);
    ESP.restart();
  }
}

// ── Task: GPS (Core 0)
void TaskGPS(void* pv) {
  for (;;) {
    if (WiFi.status() != WL_CONNECTED) {
      vTaskDelay(pdMS_TO_TICKS(100));
      continue;
    }
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

// ── Task: Web OTA + ArduinoOTA (Core 0)
void TaskOTA(void* pv) {
  for (;;) {
    if (WiFi.status() != WL_CONNECTED) {
      vTaskDelay(pdMS_TO_TICKS(500));
      continue;
    }
    otaServer.handleClient();
    ArduinoOTA.handle();
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

// ── Task: MPU + motion (4-state) + push + MOSFET (Core 1)
void TaskMPU(void* pv) {
  static uint32_t lastPushMs    = 0;
  static uint32_t lastMosfetMs  = 0;

  // Motion state
  static float    deltaHistory[DELTA_SAMPLES] = {};
  static int      deltaIdx      = 0;
  static float    prevMag       = 1.0f;
  static uint32_t lastActiveMs  = 0;
  static uint32_t roughUntilMs  = 0;
  static const char* motionState = "still";

  for (;;) {
    // ── WiFi reconnection logic: pause MPU/GPS and focus on reconnecting ──
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("\n[WiFi] Disconnected. Pausing MPU and GPS tasks. Focusing on reconnection...");
      WiFi.disconnect(true);
      delay(100);
      WiFi.begin(WIFI_SSID, WIFI_PASS);
      
      uint32_t reconnectStart = millis();
      uint32_t lastBegin = reconnectStart;
      while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
        
        // If disconnected for more than 3 minutes, reboot the ESP32 to guarantee a clean reconnect
        if (millis() - reconnectStart > 180000) {
          Serial.println("\n[WiFi] Reconnection failed after 3 minutes. Rebooting ESP32...");
          ESP.restart();
        }
        
        // Retry WiFi.begin every 30 seconds
        if (millis() - lastBegin > 30000) {
          Serial.println("\n[WiFi] Connection attempt timed out. Retrying WiFi.begin...");
          WiFi.disconnect(true);
          delay(100);
          WiFi.begin(WIFI_SSID, WIFI_PASS);
          lastBegin = millis();
        }
      }
      Serial.printf("\n[WiFi] Reconnected. IP: %s\n", WiFi.localIP().toString().c_str());
      // Reset timers to avoid immediate polling/pushing
      lastPushMs = millis();
      lastMosfetMs = millis();
      continue;
    }

    // ── Read MPU ──
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

    uint32_t now = millis();

    // ── 4-state motion detection ──
    float mag   = sqrtf(ax_*ax_ + ay_*ay_ + az_*az_);
    float delta = fabsf(mag - prevMag);
    prevMag     = mag;

    // Rolling average of delta over last 8 samples (800ms window)
    deltaHistory[deltaIdx % DELTA_SAMPLES] = delta;
    deltaIdx++;
    float avgDelta = 0;
    for (int i = 0; i < DELTA_SAMPLES; i++) avgDelta += deltaHistory[i];
    avgDelta /= DELTA_SAMPLES;

    bool gpsMoving = snap.gps_valid && snap.speed_kmph > GPS_MOVING_KMH;

    // Rough: big instant jolt — stays "rough" for ROUGH_HOLD_MS
    if (delta > ROUGH_THRESHOLD)
      roughUntilMs = now + ROUGH_HOLD_MS;

    // Track last time vehicle was clearly active
    if (gpsMoving || avgDelta > IDLE_THRESHOLD)
      lastActiveMs = now;

    // State machine
    const char* prevState = motionState;
    if (now < roughUntilMs) {
      motionState = "rough";
    } else if (gpsMoving || avgDelta > IDLE_THRESHOLD) {
      motionState = "moving";
    } else if (avgDelta < STILL_THRESHOLD && (now - lastActiveMs) > STILL_CONFIRM_MS) {
      motionState = "still";
    } else {
      motionState = "idle";
    }

    if (motionState != prevState)
      Serial.printf("[MOTION] --> %s\n", motionState);

    Serial.printf("[GPS] %s Lat:%.6f Lng:%.6f Spd:%.1f  [%s]\n",
      snap.gps_valid ? "OK" : "--", snap.lat, snap.lng, snap.speed_kmph, motionState);
    Serial.printf("[ACC] X:%.3fg Y:%.3fg Z:%.3fg  [GYR] X:%.1f Y:%.1f Z:%.1f  avgD:%.3fg\n",
      snap.ax, snap.ay, snap.az, snap.gx, snap.gy, snap.gz, avgDelta);

    // WiFi reconnection is handled at the start of TaskMPU.

    // ── Push GPS + MPU + motion every 5s ──
    if ((now - lastPushMs) >= 5000 && WiFi.status() == WL_CONNECTED) {
      lastPushMs = now;
      char body[160];
      snprintf(body, sizeof(body),
        "{\"lat\":%.6f,\"lng\":%.6f,\"speed\":%.2f,\"motion\":\"%s\"}",
        snap.lat, snap.lng, snap.speed_kmph, motionState);
      HTTPClient http;
      http.begin(String(SERVER_HOST) + "/api/truck_gps/" + TRUCK_NAME);
      http.addHeader("Content-Type", "application/json");
      int code = http.POST(body);
      Serial.printf("[PUSH] HTTP %d | %s | motion=%s\n\n", code, TRUCK_NAME, motionState);
      http.end();
    }

    // ── Poll MOSFET state every 3s ──
    if ((now - lastMosfetMs) >= 3000 && WiFi.status() == WL_CONNECTED) {
      lastMosfetMs = now;
      HTTPClient http;
      http.begin(String(SERVER_HOST) + "/api/mosfet_state/" + TRUCK_NAME);
      int code = http.GET();
      if (code == 200) {
        String resp = http.getString();
        int idx = resp.indexOf("\"state\"");
        if (idx >= 0) {
          int colonIdx = resp.indexOf(':', idx);
          if (colonIdx >= 0) {
            int searchIdx = colonIdx + 1;
            while (searchIdx < resp.length() && (resp.charAt(searchIdx) == ' ' || resp.charAt(searchIdx) == '\r' || resp.charAt(searchIdx) == '\n' || resp.charAt(searchIdx) == '\t')) {
              searchIdx++;
            }
            if (searchIdx < resp.length()) {
              int val = (resp.charAt(searchIdx) == '1') ? 1 : 0;
              digitalWrite(MOSFET_PIN, val ? HIGH : LOW);
              Serial.printf("[MOSFET] %s\n", val ? "ON" : "OFF");
            }
          }
        }
      }
      http.end();
    }

    vTaskDelay(pdMS_TO_TICKS(100));
  }
}

void setup() {
  Serial.begin(115200);
  Serial.println("Booting...");

  pinMode(MOSFET_PIN, OUTPUT);
  digitalWrite(MOSFET_PIN, LOW);

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
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.printf("Connecting as %s ", TRUCK_NAME);
  uint32_t wifiStart = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(500); Serial.print(".");
    if (millis() - wifiStart > 30000) {
      Serial.println("\nTimeout! Rebooting...");
      ESP.restart();
    }
  }
  Serial.printf("\nIP: %s\n", WiFi.localIP().toString().c_str());

  otaServer.on("/",          HTTP_GET,  handleOTAPage);
  otaServer.on("/update",    HTTP_GET,  handleOTAPage);
  otaServer.on("/do_update", HTTP_POST, handleOTAResult, handleOTAUpload);
  otaServer.begin();
  Serial.printf("Web OTA ready -> http://%s/update\n", WiFi.localIP().toString().c_str());

  ArduinoOTA.onStart([]() { Serial.println("OTA Start"); })
    .onEnd([]() { Serial.println("OTA End"); })
    .onProgress([](unsigned int p, unsigned int t) {
      if (millis() - last_ota_time > 500) {
        Serial.printf("OTA: %u%%\n", p / (t / 100));
        last_ota_time = millis();
      }
    })
    .onError([](ota_error_t e) { Serial.printf("OTA Error[%u]\n", e); });
  ArduinoOTA.begin();

  dataMutex = xSemaphoreCreateMutex();
  xTaskCreatePinnedToCore(TaskGPS, "GPS", 8192, NULL, 2, NULL, 0);
  xTaskCreatePinnedToCore(TaskOTA, "OTA", 4096, NULL, 1, NULL, 0);
  xTaskCreatePinnedToCore(TaskMPU, "MPU", 8192, NULL, 1, NULL, 1);
}

void loop() { vTaskDelay(portMAX_DELAY); }
