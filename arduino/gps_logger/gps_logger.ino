/*
 * gps_logger.ino
 * Reads NMEA sentences from an L76X GPS hat (SoftwareSerial on pins 3/4),
 * parses lat/lon/speed/time from $GPRMC, and streams CSV records over USB.
 *
 * Serial commands from host (send as single-char + newline):
 *   S  – start logging
 *   P  – pause/stop logging
 *   C  – clear session counter
 *   ?  – print status
 *
 * Output format (when logging and fix is valid):
 *   $LOG,<iso_timestamp>,<lat_dd>,<lon_dd>,<speed_kmh>,<hdop>\n
 *
 * Connect L76X TX → Arduino pin 4 (RX)
 *             RX → Arduino pin 3 (TX)  [optional, for sending PMTK commands]
 */

#include <SoftwareSerial.h>

#define GPS_RX_PIN 4
#define GPS_TX_PIN 3
#define GPS_BAUD   9600
#define USB_BAUD   115200

SoftwareSerial gpsSerial(GPS_RX_PIN, GPS_TX_PIN);

bool logging = false;
unsigned long recordCount = 0;
char nmea[100];
uint8_t nmea_idx = 0;

// ── NMEA helpers ─────────────────────────────────────────────────────────────

// Extract the Nth comma-delimited field from a NMEA sentence into buf.
bool nmeaField(const char *sentence, uint8_t fieldNum, char *buf, uint8_t bufLen) {
  uint8_t f = 0;
  uint8_t i = 0;
  uint8_t o = 0;
  while (sentence[i]) {
    if (sentence[i] == ',') {
      if (f == fieldNum) { buf[o] = '\0'; return o > 0; }
      f++; o = 0; i++; continue;
    }
    if (f == fieldNum && o < bufLen - 1) buf[o++] = sentence[i];
    i++;
  }
  if (f == fieldNum) { buf[o] = '\0'; return o > 0; }
  return false;
}

// Convert NMEA dddmm.mmmm → decimal degrees.
double nmeaToDeg(const char *raw, char hemi) {
  if (!raw || raw[0] == '\0') return 0.0;
  double val = atof(raw);
  int deg = (int)(val / 100);
  double minutes = val - deg * 100.0;
  double dd = deg + minutes / 60.0;
  if (hemi == 'S' || hemi == 'W') dd = -dd;
  return dd;
}

// Verify NMEA checksum (everything between $ and *).
bool nmeaChecksum(const char *s) {
  if (s[0] != '$') return false;
  uint8_t calc = 0;
  uint8_t i = 1;
  while (s[i] && s[i] != '*') calc ^= (uint8_t)s[i++];
  if (s[i] != '*') return false;
  uint8_t given = strtol(&s[i + 1], nullptr, 16);
  return calc == given;
}

// ── GPRMC parser ─────────────────────────────────────────────────────────────

struct Fix {
  bool valid;
  double lat, lon, speed_kmh;
  char timestamp[21]; // "2025-06-14T13:45:22Z"
};

Fix parseGPRMC(const char *s) {
  Fix f = {};
  if (!nmeaChecksum(s)) return f;

  char type[7]; nmeaField(s, 0, type, sizeof(type));
  // Accept $GPRMC or $GNRMC
  if (strcmp(type, "$GPRMC") != 0 && strcmp(type, "$GNRMC") != 0) return f;

  char status[2]; nmeaField(s, 2, status, sizeof(status));
  if (status[0] != 'A') return f; // no fix

  char timeF[10], dateF[7];
  char latF[12], latH[2], lonF[13], lonH[2], speedF[10];
  nmeaField(s, 1, timeF,  sizeof(timeF));
  nmeaField(s, 3, latF,   sizeof(latF));
  nmeaField(s, 4, latH,   sizeof(latH));
  nmeaField(s, 5, lonF,   sizeof(lonF));
  nmeaField(s, 6, lonH,   sizeof(lonH));
  nmeaField(s, 7, speedF, sizeof(speedF));
  nmeaField(s, 9, dateF,  sizeof(dateF));

  f.lat      = nmeaToDeg(latF,  latH[0]);
  f.lon      = nmeaToDeg(lonF,  lonH[0]);
  f.speed_kmh = atof(speedF) * 1.852; // knots → km/h

  // Build ISO timestamp from HHMMSS.ss + DDMMYY
  if (strlen(timeF) >= 6 && strlen(dateF) == 6) {
    snprintf(f.timestamp, sizeof(f.timestamp),
             "20%c%c-%c%c-%c%cT%c%c:%c%c:%c%cZ",
             dateF[4], dateF[5],                 // year
             dateF[2], dateF[3],                 // month
             dateF[0], dateF[1],                 // day
             timeF[0], timeF[1],                 // hour
             timeF[2], timeF[3],                 // min
             timeF[4], timeF[5]);                // sec
  } else {
    strcpy(f.timestamp, "1970-01-01T00:00:00Z");
  }

  f.valid = true;
  return f;
}

// ── Arduino lifecycle ─────────────────────────────────────────────────────────

void setup() {
  Serial.begin(USB_BAUD);
  gpsSerial.begin(GPS_BAUD);
  Serial.println(F("# Spreader GPS Logger ready"));
  Serial.println(F("# Commands: S=start P=pause C=clear ?=status"));
  Serial.println(F("# Output: $LOG,timestamp,lat,lon,speed_kmh"));
}

void loop() {
  // Handle commands from host
  if (Serial.available()) {
    char cmd = Serial.read();
    if (cmd == 'S' || cmd == 's') { logging = true;  Serial.println(F("# Logging STARTED")); }
    if (cmd == 'P' || cmd == 'p') { logging = false; Serial.println(F("# Logging PAUSED")); }
    if (cmd == 'C' || cmd == 'c') { recordCount = 0; Serial.println(F("# Counter CLEARED")); }
    if (cmd == '?') {
      Serial.print(F("# Status: ")); Serial.println(logging ? F("logging") : F("paused"));
      Serial.print(F("# Records: ")); Serial.println(recordCount);
    }
  }

  // Read one character at a time from GPS
  while (gpsSerial.available()) {
    char c = gpsSerial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      nmea[nmea_idx] = '\0';
      nmea_idx = 0;

      if (logging) {
        Fix fix = parseGPRMC(nmea);
        if (fix.valid) {
          Serial.print(F("$LOG,"));
          Serial.print(fix.timestamp);
          Serial.print(',');
          Serial.print(fix.lat,  7);
          Serial.print(',');
          Serial.print(fix.lon,  7);
          Serial.print(',');
          Serial.print(fix.speed_kmh, 2);
          Serial.print('\n');
          recordCount++;
        }
      }
    } else if (nmea_idx < sizeof(nmea) - 1) {
      nmea[nmea_idx++] = c;
    }
  }
}
