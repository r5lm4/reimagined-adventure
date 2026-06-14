/*
 * gps_logger.ino  –  Standalone SD card GPS logger for a broadcast spreader
 *
 * Hardware connections
 * ───────────────────
 * L76X GPS hat
 *   TX  →  Arduino pin 7  (SoftwareSerial RX)
 *   RX  →  Arduino pin 8  (SoftwareSerial TX, optional)
 *
 * SD card hat (SPI)
 *   CS   →  pin 10   (change SD_CS_PIN if your hat uses a different pin)
 *   MOSI →  pin 11   (hardware SPI, fixed)
 *   MISO →  pin 12   (hardware SPI, fixed)
 *   SCK  →  pin 13   (hardware SPI, fixed)
 *
 * Button  →  pin 2, other leg to GND  (toggles logging on/off)
 * LED     →  pin 6 + 220 Ω to GND    (status indicator)
 *
 * LED status
 * ──────────
 *   Slow blink (1 s)   Waiting for GPS fix
 *   Fast blink (0.2 s) Logging (fix acquired)
 *   Solid ON            SD write error – remove and re-insert card
 *   OFF                 Paused / idle (fix present but not logging)
 *
 * File naming
 * ───────────
 * Each press of the button starts a new session file: LOG001.CSV, LOG002.CSV …
 * Files are plain CSV with header: timestamp,lat,lon,speed_kmh
 * Copy the entire card to your PC and run the Python app on any CSV.
 *
 * Libraries required (install via Arduino Library Manager)
 *   SD          – built-in (ships with Arduino IDE)
 *   SoftwareSerial – built-in
 */

#include <SoftwareSerial.h>
#include <SD.h>
#include <SPI.h>

// ── Pin assignments ───────────────────────────────────────────────────────────
#define GPS_RX_PIN  7     // connects to L76X TX
#define GPS_TX_PIN  8     // connects to L76X RX (can leave unconnected)
#define SD_CS_PIN   10
#define BTN_PIN     2
#define LED_PIN     6

// ── Timing ────────────────────────────────────────────────────────────────────
#define GPS_BAUD    9600
#define LOG_INTERVAL_MS 1000   // write a fix at most once per second

// ── Globals ───────────────────────────────────────────────────────────────────
SoftwareSerial gpsSerial(GPS_RX_PIN, GPS_TX_PIN);

bool     logging      = false;
bool     hasFix       = false;
bool     sdOK         = false;
uint16_t sessionNum   = 0;
uint32_t recordCount  = 0;
uint32_t lastLogMs    = 0;

char     nmea[100];
uint8_t  nmeaIdx      = 0;

File     logFile;

// Button debounce
bool     lastBtnState  = HIGH;
uint32_t lastDebounceMs = 0;
#define  DEBOUNCE_MS    50

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

// ── Fix struct and parser ────────────────────────────────────────────────────

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

// ── SD helpers ────────────────────────────────────────────────────────────────

// Find the next unused LOGxxx.CSV filename.
uint16_t nextSessionNumber() {
  for (uint16_t n = 1; n <= 999; n++) {
    char name[13];
    snprintf(name, sizeof(name), "LOG%03u.CSV", n);
    if (!SD.exists(name)) return n;
  }
  return 999; // wrap-around; existing file will be appended
}

bool openSession(uint16_t n) {
  char name[13];
  snprintf(name, sizeof(name), "LOG%03u.CSV", n);
  logFile = SD.open(name, FILE_WRITE);
  if (!logFile) return false;
  // Write CSV header only for a new (empty) file
  if (logFile.size() == 0)
    logFile.println(F("timestamp,lat,lon,speed_kmh"));
  logFile.flush();
  return true;
}

// ── LED helpers ───────────────────────────────────────────────────────────────

void updateLED() {
  if (!sdOK) {
    // SD error – solid ON
    digitalWrite(LED_PIN, HIGH);
    return;
  }
  uint32_t t = millis();
  if (!hasFix) {
    // Slow blink: 500 ms on / 500 ms off
    digitalWrite(LED_PIN, (t % 1000) < 500 ? HIGH : LOW);
  } else if (logging) {
    // Fast blink: 100 ms on / 100 ms off
    digitalWrite(LED_PIN, (t % 200) < 100 ? HIGH : LOW);
  } else {
    digitalWrite(LED_PIN, LOW);
  }
}

// ── Button handling ───────────────────────────────────────────────────────────

void handleButton() {
  bool state = digitalRead(BTN_PIN);
  if (state != lastBtnState) lastDebounceMs = millis();
  if ((millis() - lastDebounceMs) > DEBOUNCE_MS && state == LOW && lastBtnState == HIGH) {
    // Falling edge after debounce = button pressed
    if (!logging) {
      sessionNum = nextSessionNumber();
      recordCount = 0;
      if (openSession(sessionNum)) {
        logging = true;
      } else {
        sdOK = false; // SD error
      }
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

  gpsSerial.begin(GPS_BAUD);

  if (!SD.begin(SD_CS_PIN)) {
    Serial.println(F("SD init failed! Check card and CS pin."));
    sdOK = false;
  } else {
    Serial.println(F("SD ready."));
    sdOK = true;
  }

  Serial.println(F("Press button to start/stop a session."));
}

void loop() {
  handleButton();
  updateLED();

  // Read one GPS character per loop iteration
  if (gpsSerial.available()) {
    char c = gpsSerial.read();
    if (c == '\r') return;
    if (c == '\n') {
      nmea[nmeaIdx] = '\0';
      nmeaIdx = 0;

      Fix fix = parseGPRMC(nmea);
      if (fix.valid) {
        hasFix = true;

        if (logging && sdOK && (millis() - lastLogMs >= LOG_INTERVAL_MS)) {
          lastLogMs = millis();

          if (!logFile) {
            // Re-open if closed unexpectedly
            if (!openSession(sessionNum)) { sdOK = false; return; }
          }

          logFile.print(fix.timestamp);
          logFile.print(',');
          logFile.print(fix.lat, 7);
          logFile.print(',');
          logFile.print(fix.lon, 7);
          logFile.print(',');
          logFile.println(fix.speed_kmh, 2);
          logFile.flush();   // flush every record so data survives power loss

          recordCount++;

          // Mirror to USB serial so you can confirm it's working
          Serial.print(F("LOG "));
          Serial.print(recordCount);
          Serial.print(F(" | "));
          Serial.print(fix.timestamp);
          Serial.print(F(" | "));
          Serial.print(fix.lat, 6);
          Serial.print(F(", "));
          Serial.println(fix.lon, 6);
        }
      }
    } else if (nmeaIdx < sizeof(nmea) - 1) {
      nmea[nmeaIdx++] = c;
    }
  }
}
