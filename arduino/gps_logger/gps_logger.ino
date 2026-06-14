/*
 * gps_logger.ino  –  Standalone SD card GPS logger for a broadcast spreader
 *                    with 128×64 OLED property-boundary map
 *
 * Hardware connections
 * ───────────────────
 * L76X GPS hat
 *   TX  →  pin 7  (SoftwareSerial RX)
 *   RX  →  pin 8  (SoftwareSerial TX, optional)
 *
 * SD card hat (SPI)
 *   CS   →  pin 10  (change SD_CS_PIN below if different)
 *   MOSI →  pin 11  (hardware SPI)
 *   MISO →  pin 12  (hardware SPI)
 *   SCK  →  pin 13  (hardware SPI)
 *
 * SSD1306 OLED (I2C, 128×64)
 *   SDA  →  A4
 *   SCL  →  A5
 *   VCC  →  3.3 V or 5 V
 *   GND  →  GND
 *
 * Button  →  pin 2, other leg to GND  (start/stop session)
 * LED     →  pin 6 + 220 Ω to GND    (status)
 *
 * LED status
 * ──────────
 *   Slow blink (1 s)   No GPS fix
 *   Fast blink (0.2 s) Logging
 *   Off                Ready / paused
 *   Solid ON           SD error
 *
 * SD card files
 * ─────────────
 *   BOUNDARY.CSV  –  property boundary from Python app (lat,lon pairs)
 *                    Put this on the card before going outside.
 *   LOG001.CSV … –  session logs created by button press
 *
 * OLED map
 * ────────
 *   The 96×64 left portion of the screen is the map area.
 *   Dashed line = property boundary (loaded from BOUNDARY.CSV)
 *   Filled dot  = current GPS position
 *   Dot trail   = where you've already been this session
 *   Right 32×64 strip = status text (fix, session #, record count)
 *
 * Libraries  (install via Arduino Library Manager)
 *   Adafruit SSD1306
 *   Adafruit GFX Library
 *   SD          – built-in
 *   SoftwareSerial – built-in
 */

#include <SoftwareSerial.h>
#include <SD.h>
#include <SPI.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ── Configuration ─────────────────────────────────────────────────────────────
#define GPS_RX_PIN      7
#define GPS_TX_PIN      8
#define SD_CS_PIN       10
#define BTN_PIN         2
#define LED_PIN         6
#define GPS_BAUD        9600
#define LOG_INTERVAL_MS 1000

// OLED
#define OLED_WIDTH      128
#define OLED_HEIGHT     64
#define OLED_ADDR       0x3C   // most common; try 0x3D if display is blank

// Map viewport occupies the left 96 columns; status strip uses remaining 32.
#define MAP_W           96
#define MAP_H           64
#define STATUS_X        97     // left edge of the status text strip

// Memory budget: keep these small.
// On an Uno the SSD1306 frame buffer alone uses 1 KB of the 2 KB SRAM.
#define MAX_BOUNDARY_PTS  24   // property outline points (scaled to pixels)
#define MAX_TRACK_PTS     48   // rolling trail of where you've walked

// ── Globals ───────────────────────────────────────────────────────────────────
SoftwareSerial          gpsSerial(GPS_RX_PIN, GPS_TX_PIN);
Adafruit_SSD1306        oled(OLED_WIDTH, OLED_HEIGHT, &Wire, -1);

bool      logging        = false;
bool      hasFix         = false;
bool      sdOK           = false;
bool      oledOK         = false;
uint16_t  sessionNum     = 0;
uint32_t  recordCount    = 0;
uint32_t  lastLogMs      = 0;
uint32_t  lastDisplayMs  = 0;
#define   DISPLAY_INTERVAL_MS 500

char      nmea[100];
uint8_t   nmeaIdx        = 0;
File      logFile;

// Button debounce
bool      lastBtnState   = HIGH;
uint32_t  lastDebounceMs = 0;
#define   DEBOUNCE_MS    50

// ── Map data ──────────────────────────────────────────────────────────────────
// Property boundary stored as pixel coords after scaling.
uint8_t   bndX[MAX_BOUNDARY_PTS], bndY[MAX_BOUNDARY_PTS];
uint8_t   bndCount = 0;

// Rolling track trail (pixel coords, circular buffer).
uint8_t   trailX[MAX_TRACK_PTS], trailY[MAX_TRACK_PTS];
uint8_t   trailHead = 0, trailCount = 0;

// Bounding box in degrees, set when BOUNDARY.CSV is loaded.
// Used to project lat/lon to map pixel coords.
double    mapMinLat, mapMaxLat, mapMinLon, mapMaxLon;
bool      mapReady = false;

// Current position in pixel coords.
int8_t    curPX = -1, curPY = -1;

// ── NMEA helpers ──────────────────────────────────────────────────────────────

bool nmeaField(const char *s, uint8_t n, char *buf, uint8_t len) {
  uint8_t f = 0, i = 0, o = 0;
  while (s[i]) {
    if (s[i] == ',') {
      if (f == n) { buf[o] = '\0'; return o > 0; }
      f++; o = 0; i++; continue;
    }
    if (f == n && o < len - 1) buf[o++] = s[i];
    i++;
  }
  if (f == n) { buf[o] = '\0'; return o > 0; }
  return false;
}

double nmeaToDeg(const char *raw, char hemi) {
  if (!raw[0]) return 0.0;
  double v = atof(raw);
  int d = (int)(v / 100);
  double dd = d + (v - d * 100.0) / 60.0;
  if (hemi == 'S' || hemi == 'W') dd = -dd;
  return dd;
}

bool nmeaChecksum(const char *s) {
  if (s[0] != '$') return false;
  uint8_t calc = 0, i = 1;
  while (s[i] && s[i] != '*') calc ^= (uint8_t)s[i++];
  if (s[i] != '*') return false;
  return calc == (uint8_t)strtol(&s[i + 1], nullptr, 16);
}

// ── Fix struct ────────────────────────────────────────────────────────────────

struct Fix {
  bool   valid;
  double lat, lon, speed_kmh;
  char   timestamp[21];
};

Fix parseGPRMC(const char *s) {
  Fix f = {};
  if (!nmeaChecksum(s)) return f;
  char type[7]; nmeaField(s, 0, type, sizeof(type));
  if (strcmp(type, "$GPRMC") != 0 && strcmp(type, "$GNRMC") != 0) return f;
  char status[2]; nmeaField(s, 2, status, sizeof(status));
  if (status[0] != 'A') return f;

  char timeF[10], dateF[7], latF[12], latH[2], lonF[13], lonH[2], speedF[10];
  nmeaField(s, 1, timeF,  sizeof(timeF));
  nmeaField(s, 3, latF,   sizeof(latF));
  nmeaField(s, 4, latH,   sizeof(latH));
  nmeaField(s, 5, lonF,   sizeof(lonF));
  nmeaField(s, 6, lonH,   sizeof(lonH));
  nmeaField(s, 7, speedF, sizeof(speedF));
  nmeaField(s, 9, dateF,  sizeof(dateF));

  f.lat       = nmeaToDeg(latF, latH[0]);
  f.lon       = nmeaToDeg(lonF, lonH[0]);
  f.speed_kmh = atof(speedF) * 1.852;

  if (strlen(timeF) >= 6 && strlen(dateF) == 6) {
    snprintf(f.timestamp, sizeof(f.timestamp),
             "20%c%c-%c%c-%c%cT%c%c:%c%c:%c%cZ",
             dateF[4], dateF[5], dateF[2], dateF[3], dateF[0], dateF[1],
             timeF[0], timeF[1], timeF[2], timeF[3], timeF[4], timeF[5]);
  } else {
    strcpy(f.timestamp, "1970-01-01T00:00:00Z");
  }
  f.valid = true;
  return f;
}

// ── Coordinate → pixel projection ─────────────────────────────────────────────

// Project a lat/lon to MAP_W × MAP_H pixel space.
// Returns false if the point is outside the bounding box.
bool project(double lat, double lon, uint8_t &px, uint8_t &py) {
  if (!mapReady) return false;
  double latRange = mapMaxLat - mapMinLat;
  double lonRange = mapMaxLon - mapMinLon;
  if (latRange == 0 || lonRange == 0) return false;

  int16_t x = (int16_t)((lon - mapMinLon) / lonRange * (MAP_W - 1));
  // Latitude increases upward on Earth, downward on screen.
  int16_t y = (int16_t)((mapMaxLat - lat) / latRange * (MAP_H - 1));

  if (x < 0 || x >= MAP_W || y < 0 || y >= MAP_H) return false;
  px = (uint8_t)x;
  py = (uint8_t)y;
  return true;
}

// ── Boundary loader ───────────────────────────────────────────────────────────

void loadBoundary() {
  if (!sdOK || !SD.exists("BOUNDARY.CSV")) {
    Serial.println(F("BOUNDARY.CSV not found"));
    return;
  }

  File f = SD.open("BOUNDARY.CSV");
  if (!f) return;

  // First pass: find bounding box and read raw lat/lon into temporary arrays.
  // We use two passes to avoid needing a double-sized buffer.
  // Instead, read once to get bbox, then re-read to project.
  double lats[MAX_BOUNDARY_PTS], lons[MAX_BOUNDARY_PTS];
  uint8_t count = 0;
  mapMinLat =  90.0; mapMaxLat = -90.0;
  mapMinLon = 180.0; mapMaxLon = -180.0;

  char line[32];
  // Skip header
  f.readBytesUntil('\n', line, sizeof(line));

  while (f.available() && count < MAX_BOUNDARY_PTS) {
    uint8_t len = f.readBytesUntil('\n', line, sizeof(line) - 1);
    line[len] = '\0';
    char *comma = strchr(line, ',');
    if (!comma) continue;
    *comma = '\0';
    double lat = atof(line);
    double lon = atof(comma + 1);
    if (lat == 0.0 && lon == 0.0) continue;
    lats[count] = lat;
    lons[count] = lon;
    if (lat < mapMinLat) mapMinLat = lat;
    if (lat > mapMaxLat) mapMaxLat = lat;
    if (lon < mapMinLon) mapMinLon = lon;
    if (lon > mapMaxLon) mapMaxLon = lon;
    count++;
  }
  f.close();

  if (count < 3) { Serial.println(F("BOUNDARY.CSV too short")); return; }

  // Add a small margin (≈5 m) so the boundary doesn't touch the screen edge.
  double latM = (mapMaxLat - mapMinLat) * 0.08;
  double lonM = (mapMaxLon - mapMinLon) * 0.08;
  mapMinLat -= latM; mapMaxLat += latM;
  mapMinLon -= lonM; mapMaxLon += lonM;
  mapReady = true;

  // Second pass: project to pixel coords.
  bndCount = 0;
  for (uint8_t i = 0; i < count; i++) {
    uint8_t px, py;
    if (project(lats[i], lons[i], px, py))
      { bndX[bndCount] = px; bndY[bndCount] = py; bndCount++; }
  }

  Serial.print(F("Boundary loaded: "));
  Serial.print(bndCount);
  Serial.println(F(" points"));
}

// ── SD session helpers ────────────────────────────────────────────────────────

uint16_t nextSessionNumber() {
  for (uint16_t n = 1; n <= 999; n++) {
    char name[13];
    snprintf(name, sizeof(name), "LOG%03u.CSV", n);
    if (!SD.exists(name)) return n;
  }
  return 999;
}

bool openSession(uint16_t n) {
  char name[13];
  snprintf(name, sizeof(name), "LOG%03u.CSV", n);
  logFile = SD.open(name, FILE_WRITE);
  if (!logFile) return false;
  if (logFile.size() == 0)
    logFile.println(F("timestamp,lat,lon,speed_kmh"));
  logFile.flush();
  return true;
}

// ── OLED rendering ────────────────────────────────────────────────────────────

void drawMap() {
  if (!oledOK) return;
  oled.clearDisplay();

  // ── Map area (left 96 px) ──────────────────────────────────────────────────

  if (mapReady && bndCount >= 2) {
    // Draw property boundary as a dashed polygon.
    for (uint8_t i = 0; i < bndCount; i++) {
      uint8_t j = (i + 1) % bndCount;
      int16_t dx = (int16_t)bndX[j] - bndX[i];
      int16_t dy = (int16_t)bndY[j] - bndY[i];
      // Simple dash: draw every other segment of 3 px.
      float len = sqrt((float)dx*dx + (float)dy*dy);
      if (len < 1) continue;
      float nx = dx / len, ny = dy / len;
      float t = 0;
      bool draw = true;
      while (t < len) {
        float t2 = t + 3.0f;
        if (t2 > len) t2 = len;
        if (draw) {
          oled.drawLine(
            bndX[i] + (int16_t)(t  * nx),
            bndY[i] + (int16_t)(t  * ny),
            bndX[i] + (int16_t)(t2 * nx),
            bndY[i] + (int16_t)(t2 * ny),
            SSD1306_WHITE);
        }
        t = t2 + 3.0f;
        draw = !draw;
      }
    }
  } else if (!mapReady) {
    // No boundary file – show a placeholder frame.
    oled.drawRect(1, 1, MAP_W - 2, MAP_H - 2, SSD1306_WHITE);
    oled.setCursor(4, 28);
    oled.setTextSize(1);
    oled.print(F("No BOUNDARY"));
    oled.setCursor(4, 38);
    oled.print(F(".CSV on card"));
  }

  // Track trail dots.
  for (uint8_t i = 0; i < trailCount; i++) {
    oled.drawPixel(trailX[i], trailY[i], SSD1306_WHITE);
  }

  // Current position: filled 3×3 square.
  if (curPX >= 0) {
    oled.fillRect(curPX - 1, curPY - 1, 3, 3, SSD1306_WHITE);
  }

  // Divider between map and status strip.
  oled.drawFastVLine(MAP_W, 0, MAP_H, SSD1306_WHITE);

  // ── Status strip (right 31 px) ────────────────────────────────────────────
  oled.setTextSize(1);
  oled.setTextColor(SSD1306_WHITE);

  // Fix indicator.
  oled.setCursor(STATUS_X, 0);
  oled.print(hasFix ? F("FIX") : F("---"));

  // Session number.
  oled.setCursor(STATUS_X, 10);
  oled.print(F("S"));
  if (logging)
    oled.print(sessionNum);
  else
    oled.print(F("-"));

  // Record count (up to 4 digits).
  oled.setCursor(STATUS_X, 20);
  if (recordCount < 10000)
    oled.print(recordCount);
  else
    oled.print(F("999+"));

  // Logging / ready indicator.
  oled.setCursor(STATUS_X, 34);
  if (!sdOK)        oled.print(F("SDERR"));
  else if (logging) oled.print(F("LOG"));
  else if (hasFix)  oled.print(F("RDY"));
  else              oled.print(F("WAIT"));

  // Blink a dot while logging so you know the display is live.
  if (logging && (millis() % 1000) < 500) {
    oled.fillCircle(STATUS_X + 12, 56, 3, SSD1306_WHITE);
  }

  oled.display();
}

// ── LED ───────────────────────────────────────────────────────────────────────

void updateLED() {
  if (!sdOK) { digitalWrite(LED_PIN, HIGH); return; }
  uint32_t t = millis();
  if (!hasFix)       digitalWrite(LED_PIN, (t % 1000) < 500  ? HIGH : LOW);
  else if (logging)  digitalWrite(LED_PIN, (t % 200)  < 100  ? HIGH : LOW);
  else               digitalWrite(LED_PIN, LOW);
}

// ── Button ────────────────────────────────────────────────────────────────────

void handleButton() {
  bool state = digitalRead(BTN_PIN);
  if (state != lastBtnState) lastDebounceMs = millis();
  if ((millis() - lastDebounceMs) > DEBOUNCE_MS && state == LOW && lastBtnState == HIGH) {
    if (!logging) {
      sessionNum  = nextSessionNumber();
      recordCount = 0;
      trailHead   = 0;
      trailCount  = 0;
      if (openSession(sessionNum)) logging = true;
      else sdOK = false;
    } else {
      logging = false;
      if (logFile) { logFile.flush(); logFile.close(); }
    }
  }
  lastBtnState = state;
}

// ── Arduino lifecycle ─────────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  pinMode(BTN_PIN, INPUT_PULLUP);
  pinMode(LED_PIN, OUTPUT);

  // OLED init
  if (oled.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)) {
    oledOK = true;
    oled.clearDisplay();
    oled.setTextSize(1);
    oled.setTextColor(SSD1306_WHITE);
    oled.setCursor(0, 0);
    oled.println(F("Spreader Mapper"));
    oled.println(F("Starting..."));
    oled.display();
  } else {
    Serial.println(F("OLED init failed"));
  }

  gpsSerial.begin(GPS_BAUD);

  // SD init
  if (!SD.begin(SD_CS_PIN)) {
    Serial.println(F("SD init failed"));
    sdOK = false;
    if (oledOK) {
      oled.setCursor(0, 20);
      oled.println(F("SD FAILED!"));
      oled.display();
    }
  } else {
    sdOK = true;
    Serial.println(F("SD OK"));
    loadBoundary();
  }

  if (oledOK) {
    delay(1000);
    drawMap();
  }
  Serial.println(F("Ready. Press button to start session."));
}

void loop() {
  handleButton();
  updateLED();

  // Refresh display periodically (not every loop – saves time for GPS reading).
  if (millis() - lastDisplayMs >= DISPLAY_INTERVAL_MS) {
    lastDisplayMs = millis();
    drawMap();
  }

  // GPS character reader.
  if (gpsSerial.available()) {
    char c = gpsSerial.read();
    if (c == '\r') return;
    if (c == '\n') {
      nmea[nmeaIdx] = '\0';
      nmeaIdx = 0;

      Fix fix = parseGPRMC(nmea);
      if (fix.valid) {
        hasFix = true;

        // Update map position.
        uint8_t px, py;
        if (project(fix.lat, fix.lon, px, py)) {
          curPX = px;
          curPY = py;
          // Add to trail buffer.
          if (logging) {
            trailX[trailHead] = px;
            trailY[trailHead] = py;
            trailHead = (trailHead + 1) % MAX_TRACK_PTS;
            if (trailCount < MAX_TRACK_PTS) trailCount++;
          }
        }

        // Log to SD.
        if (logging && sdOK && (millis() - lastLogMs >= LOG_INTERVAL_MS)) {
          lastLogMs = millis();
          if (!logFile && !openSession(sessionNum)) { sdOK = false; return; }

          logFile.print(fix.timestamp); logFile.print(',');
          logFile.print(fix.lat, 7);   logFile.print(',');
          logFile.print(fix.lon, 7);   logFile.print(',');
          logFile.println(fix.speed_kmh, 2);
          logFile.flush();
          recordCount++;

          Serial.print(F("LOG ")); Serial.print(recordCount);
          Serial.print(F(" | ")); Serial.print(fix.lat, 6);
          Serial.print(F(", ")); Serial.println(fix.lon, 6);
        }
      }
    } else if (nmeaIdx < sizeof(nmea) - 1) {
      nmea[nmeaIdx++] = c;
    }
  }
}
