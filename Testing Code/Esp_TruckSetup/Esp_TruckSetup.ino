#include <WiFi.h>
#include <WebServer.h>
#include <EEPROM.h>
#include <TinyGPSPlus.h>
#include <HardwareSerial.h>
#include <HTTPClient.h>

// ── EEPROM Layout ─────────────────────────────────────────
// [0]      : configured flag (0xAB = configured)
// [1..33]  : WiFi SSID     (32 chars + null)
// [34..97] : WiFi Password (64 chars + null)
// [98..130]: Truck Number  (32 chars + null)
#define EEPROM_SIZE       132
#define ADDR_FLAG         0
#define ADDR_SSID         1
#define ADDR_PASS         34
#define ADDR_TRUCK        98
#define CONFIGURED_FLAG   0xAB

// ── GPS ───────────────────────────────────────────────────
#define GPS_RX  16
#define GPS_TX  17
#define GPS_BAUD 9600
TinyGPSPlus    gps;
HardwareSerial gpsSerial(2);

// ── Server ────────────────────────────────────────────────
const char* SERVER_HOST = "http://150.129.165.162:7777";

// ── Globals ───────────────────────────────────────────────
WebServer server(80);
String truckNumber = "";
bool   isConfigured = false;

// ── EEPROM helpers ────────────────────────────────────────
void eepromWriteStr(int addr, const String& s, int maxLen) {
  for (int i = 0; i < maxLen; i++)
    EEPROM.write(addr + i, i < (int)s.length() ? s[i] : 0);
}
String eepromReadStr(int addr, int maxLen) {
  String s = "";
  for (int i = 0; i < maxLen; i++) {
    char c = EEPROM.read(addr + i);
    if (c == 0) break;
    s += c;
  }
  return s;
}

// ── Captive Portal HTML ───────────────────────────────────
const char SETUP_HTML[] PROGMEM = R"rawhtml(
<!DOCTYPE html><html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GPS Truck Setup</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:16px}
  .card{background:#1e293b;border-radius:16px;padding:28px;width:100%;max-width:420px;box-shadow:0 20px 60px #0008}
  h1{font-size:1.3rem;font-weight:700;color:#38bdf8;margin-bottom:4px}
  p{font-size:.82rem;color:#64748b;margin-bottom:22px}
  label{display:block;font-size:.78rem;font-weight:600;color:#94a3b8;margin-bottom:6px;margin-top:16px}
  input{width:100%;padding:10px 14px;background:#0f172a;border:1px solid #334155;border-radius:8px;color:#f1f5f9;font-size:.9rem;outline:none}
  input:focus{border-color:#38bdf8}
  .wifi-list{margin-top:8px;border:1px solid #334155;border-radius:8px;overflow:hidden;max-height:200px;overflow-y:auto}
  .wifi-item{padding:10px 14px;cursor:pointer;font-size:.88rem;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #1e293b;transition:background .1s}
  .wifi-item:hover{background:#263449}
  .wifi-item.selected{background:#1d3a5f;border-left:3px solid #38bdf8}
  .signal{font-size:.72rem;color:#64748b}
  .btn{width:100%;padding:12px;margin-top:22px;background:#0ea5e9;color:#fff;border:none;border-radius:8px;font-size:.95rem;font-weight:700;cursor:pointer}
  .btn:hover{background:#0284c7}
  .scanning{color:#64748b;font-size:.82rem;padding:12px;text-align:center}
  #passGroup{display:none;margin-top:0}
</style></head><body>
<div class="card">
  <h1>🚛 GPS Truck Setup</h1>
  <p>Enter truck number and select WiFi to connect</p>

  <label>Truck Number</label>
  <input type="text" id="truck" placeholder="e.g. GJ01AB1234" maxlength="32">

  <label>Select WiFi Network</label>
  <div class="wifi-list" id="wifiList"><div class="scanning">Scanning...</div></div>

  <div id="passGroup">
    <label>Password for <span id="selSsid" style="color:#38bdf8"></span></label>
    <input type="password" id="pass" placeholder="WiFi Password">
  </div>

  <button class="btn" onclick="saveConfig()">Save & Connect</button>
</div>

<script>
let selectedSsid = '';

fetch('/scan').then(r=>r.json()).then(nets=>{
  const el = document.getElementById('wifiList');
  if(!nets.length){el.innerHTML='<div class="scanning">No networks found. <a href="#" onclick="location.reload()">Retry</a></div>';return;}
  el.innerHTML = nets.map(n=>`
    <div class="wifi-item" onclick="selectWifi('${n.ssid.replace(/'/g,"\\'")}',this)">
      <span>${n.ssid}</span>
      <span class="signal">${n.rssi} dBm</span>
    </div>`).join('');
});

function selectWifi(ssid, el){
  selectedSsid = ssid;
  document.querySelectorAll('.wifi-item').forEach(e=>e.classList.remove('selected'));
  el.classList.add('selected');
  document.getElementById('selSsid').textContent = ssid;
  document.getElementById('passGroup').style.display = 'block';
}

function saveConfig(){
  const truck = document.getElementById('truck').value.trim();
  const pass  = document.getElementById('pass').value;
  if(!truck){alert('Enter truck number');return;}
  if(!selectedSsid){alert('Select a WiFi network');return;}
  fetch('/save',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:`ssid=${encodeURIComponent(selectedSsid)}&pass=${encodeURIComponent(pass)}&truck=${encodeURIComponent(truck)}`
  }).then(r=>r.text()).then(t=>{document.body.innerHTML=`<div style="color:#22c55e;font-size:1.2rem;text-align:center;margin-top:40vh">${t}</div>`;});
}
</script></body></html>
)rawhtml";

// ── AP Mode Handlers ──────────────────────────────────────
void handleRoot() {
  server.send(200, "text/html", SETUP_HTML);
}

void handleScan() {
  int n = WiFi.scanNetworks();
  String json = "[";
  for (int i = 0; i < n; i++) {
    if (i) json += ",";
    String ssid = WiFi.SSID(i);
    ssid.replace("\"", "\\\"");
    json += "{\"ssid\":\"" + ssid + "\",\"rssi\":" + String(WiFi.RSSI(i)) + "}";
  }
  json += "]";
  server.send(200, "application/json", json);
}

void handleSave() {
  String ssid  = server.arg("ssid");
  String pass  = server.arg("pass");
  String truck = server.arg("truck");

  eepromWriteStr(ADDR_SSID,  ssid,  32);
  eepromWriteStr(ADDR_PASS,  pass,  64);
  eepromWriteStr(ADDR_TRUCK, truck, 32);
  EEPROM.write(ADDR_FLAG, CONFIGURED_FLAG);
  EEPROM.commit();

  server.send(200, "text/plain", "✅ Saved! ESP is rebooting and will connect to " + ssid);
  delay(1500);
  ESP.restart();
}

// ── GPS push task (Core 0) ────────────────────────────────
SemaphoreHandle_t gpsMutex;
struct GpsSnap { float lat, lng, speed; bool valid; };
static GpsSnap gpsSnap = {0,0,0,false};

void TaskGPS(void* pv) {
  for(;;) {
    while(gpsSerial.available())
      if(gps.encode(gpsSerial.read()) && gps.location.isUpdated())
        if(xSemaphoreTake(gpsMutex, pdMS_TO_TICKS(5))==pdTRUE) {
          gpsSnap = {(float)gps.location.lat(), (float)gps.location.lng(),
                     gps.speed.isValid()?(float)gps.speed.kmph():0.0f, true};
          xSemaphoreGive(gpsMutex);
        }
    vTaskDelay(pdMS_TO_TICKS(5));
  }
}

// ── Push GPS to server (Core 1) ───────────────────────────
void TaskPush(void* pv) {
  for(;;) {
    vTaskDelay(pdMS_TO_TICKS(5000)); // push every 5 seconds

    GpsSnap snap;
    xSemaphoreTake(gpsMutex, portMAX_DELAY);
    snap = gpsSnap;
    xSemaphoreGive(gpsMutex);

    if(!snap.valid || WiFi.status()!=WL_CONNECTED) continue;

    HTTPClient http;
    String url = String(SERVER_HOST) + "/api/truck_gps/" + truckNumber;
    http.begin(url);
    http.addHeader("Content-Type", "application/json");

    char body[100];
    snprintf(body, sizeof(body), "{\"lat\":%.6f,\"lng\":%.6f,\"speed\":%.1f}",
             snap.lat, snap.lng, snap.speed);

    int code = http.POST(body);
    Serial.printf("[%s] Pushed GPS -> HTTP %d | Lat:%.6f Lng:%.6f\n",
                  truckNumber.c_str(), code, snap.lat, snap.lng);
    http.end();
  }
}

// ── SETUP ─────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  EEPROM.begin(EEPROM_SIZE);
  gpsSerial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX, GPS_TX);

  isConfigured = (EEPROM.read(ADDR_FLAG) == CONFIGURED_FLAG);

  if (!isConfigured) {
    // ── FIRST BOOT: Start AP + captive portal ──
    Serial.println("First boot — starting setup portal at 192.168.4.1");
    WiFi.mode(WIFI_AP);
    WiFi.softAP("GPS-TRUCK-SETUP");

    server.on("/",     handleRoot);
    server.on("/scan", handleScan);
    server.on("/save", HTTP_POST, handleSave);
    server.onNotFound(handleRoot); // captive portal redirect
    server.begin();

    Serial.println("Portal ready. Connect to WiFi: GPS-TRUCK-SETUP → open 192.168.4.1");

    while(true) {
      server.handleClient();
      delay(2);
    }

  } else {
    // ── NORMAL BOOT: Connect to saved WiFi ──
    String ssid  = eepromReadStr(ADDR_SSID,  32);
    String pass  = eepromReadStr(ADDR_PASS,  64);
    truckNumber  = eepromReadStr(ADDR_TRUCK, 32);

    Serial.printf("Truck: %s | Connecting to: %s\n", truckNumber.c_str(), ssid.c_str());

    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid.c_str(), pass.c_str());

    int tries = 0;
    while(WiFi.status() != WL_CONNECTED && tries < 20) {
      delay(500); Serial.print("."); tries++;
    }

    if(WiFi.status() == WL_CONNECTED)
      Serial.printf("\nConnected! IP: %s\n", WiFi.localIP().toString().c_str());
    else
      Serial.println("\nWiFi failed — will retry on each push cycle");

    gpsMutex = xSemaphoreCreateMutex();
    xTaskCreatePinnedToCore(TaskGPS,  "GPS",  8192, NULL, 2, NULL, 0);
    xTaskCreatePinnedToCore(TaskPush, "PUSH", 8192, NULL, 1, NULL, 1);
  }
}

void loop() {
  vTaskDelay(portMAX_DELAY);
}
