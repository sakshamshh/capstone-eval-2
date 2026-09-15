/*
  Ambulance Dispatch Traffic Light - ESP32 client
  --------------------------------------------------
  Polls the backend server's GET /traffic-light/state endpoint about once a
  second and drives the physical relays (Red/Yellow/Green on GPIO13/12/14)
  to match. The server only reports a target state ("idle", "yellow_flash",
  "green") - this firmware is responsible for the actual flashing behavior
  during yellow_flash, and for failing safe if the server becomes unreachable.

  BEFORE UPLOADING:
    1. Fill in SERVER_URL below with the server's actual address:
         - Local testing on the same WiFi as your laptop: http://<laptop LAN IP>:8000
         - Once deployed: the Render.com HTTPS URL
    2. Confirm WIFI_SSID/WIFI_PASSWORD match your hotspot.
    3. Confirm ACTIVE_LOW matches your relay modules (already confirmed
       active-low for this hardware earlier in this project).

  STATE MAPPING
    "idle"         -> Red on, Yellow off, Green off (default safe state)
    "yellow_flash" -> Yellow flashes on/off, Red and Green off
    "green"        -> Green on, Red and Yellow off

  RELIABILITY / FAIL-SAFE
    - If a poll fails (network drop, server down, bad response), the light
      falls back to "idle" (red) after a short grace period rather than
      getting stuck in whatever state it last saw - this matters most for
      "green", since a green light stuck on if the server disappears would
      be actively wrong, not just inconvenient.
    - Polling and flashing are both non-blocking (millis()-based), so a
      slow or failed HTTP request never freezes the flash timing or the
      WiFi reconnect logic.
*/

#include <WiFi.h>
#include <WiFiMulti.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

WiFiMulti wifiMulti;

// ---- WiFi ----
const char* WIFI_SSID = "saksham";
const char* WIFI_PASSWORD = "123456789";

// ---- Server ----
const char* SERVER_URL = "http://192.168.1.16:8000";  // <-- update to your server's actual address
const char* DEMO_KEY = "traffic-demo-2026";
const unsigned long POLL_INTERVAL_MS = 1000;
const unsigned long FAILSAFE_TIMEOUT_MS = 5000;  // if no successful poll in this long, fall back to idle

// ---- Relays ----
const int PIN_RED = 13;
const int PIN_YELLOW = 12;
const int PIN_GREEN = 14;
const bool ACTIVE_LOW = true;

// ---- Flash timing for yellow_flash ----
const unsigned long FLASH_INTERVAL_MS = 400;

// ---- State ----
String currentServerState = "idle";  // last state successfully read from server
unsigned long lastSuccessfulPollMs = 0;
unsigned long lastPollAttemptMs = 0;
unsigned long lastFlashToggleMs = 0;
bool flashOn = false;

void writeRelay(int pin, bool on) {
  digitalWrite(pin, ACTIVE_LOW ? !on : on);
}

void applyState(const String& state) {
  if (state == "green") {
    writeRelay(PIN_RED, false);
    writeRelay(PIN_YELLOW, false);
    writeRelay(PIN_GREEN, true);
  } else if (state == "yellow_flash") {
    writeRelay(PIN_RED, false);
    writeRelay(PIN_GREEN, false);
    unsigned long now = millis();
    if (now - lastFlashToggleMs >= FLASH_INTERVAL_MS) {
      flashOn = !flashOn;
      lastFlashToggleMs = now;
    }
    writeRelay(PIN_YELLOW, flashOn);
  } else {
    // idle / unknown / fail-safe default
    writeRelay(PIN_YELLOW, false);
    writeRelay(PIN_GREEN, false);
    writeRelay(PIN_RED, true);
  }
}

void pollServer() {
  if (wifiMulti.run() != WL_CONNECTED) {
    return;  // will retry next loop; fail-safe timeout handles the light state
  }

  HTTPClient http;
  String url = String(SERVER_URL) + "/traffic-light/state";
  http.begin(url);
  http.addHeader("X-Demo-Key", DEMO_KEY);
  http.setTimeout(3000);

  int code = http.GET();

  if (code == 200) {
    String body = http.getString();
    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, body);
    if (!err && doc["state"].is<const char*>()) {
      currentServerState = String((const char*)doc["state"]);
      lastSuccessfulPollMs = millis();
      Serial.print("Poll OK - state: ");
      Serial.println(currentServerState);
    } else {
      Serial.println("Poll response JSON parse failed");
    }
  } else {
    Serial.print("Poll failed, HTTP code: ");
    Serial.println(code);
  }

  http.end();
}

void setup() {
  Serial.begin(115200);

  pinMode(PIN_RED, OUTPUT);
  pinMode(PIN_YELLOW, OUTPUT);
  pinMode(PIN_GREEN, OUTPUT);
  applyState("idle");  // safe default before WiFi/server are confirmed

  wifiMulti.addAP(WIFI_SSID, WIFI_PASSWORD);

  Serial.print("Connecting to WiFi");
  unsigned long start = millis();
  while (wifiMulti.run() != WL_CONNECTED && millis() - start < 20000) {
    delay(400);
    Serial.print(".");
  }
  Serial.println();

  if (wifiMulti.run() == WL_CONNECTED) {
    Serial.print("Connected to: ");
    Serial.println(WiFi.SSID());
    Serial.print("IP address: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("WiFi not connected yet - will keep retrying in the background.");
  }

  lastSuccessfulPollMs = millis();  // don't immediately fail-safe on boot
}

void loop() {
  unsigned long now = millis();

  if (now - lastPollAttemptMs >= POLL_INTERVAL_MS) {
    lastPollAttemptMs = now;
    pollServer();
  }

  String effectiveState = currentServerState;
  if (now - lastSuccessfulPollMs > FAILSAFE_TIMEOUT_MS) {
    effectiveState = "idle";  // fail-safe: too long since a good poll, go safe
  }

  applyState(effectiveState);
}
