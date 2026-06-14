"""
gps.py - Thread that reads NMEA sentences from L76X GPS on /dev/serial0.

Parses $GPRMC and $GPGGA sentences, validates checksums, and maintains
a thread-safe shared fix dict. Handles serial disconnects with retry.
"""

import threading
import time
import serial
import logging

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_current_fix = {
    "valid": False,
    "lat": None,
    "lon": None,
    "speed_kmh": 0.0,
    "timestamp": None,
    "fix_quality": 0,
    "satellites": 0,
    "hdop": 99.9,
    "altitude_m": 0.0,
}

_running = False
_thread = None


def _validate_checksum(sentence: str) -> bool:
    """Validate NMEA checksum. Sentence should not include leading $."""
    try:
        if "*" not in sentence:
            return False
        data, checksum_str = sentence.rsplit("*", 1)
        checksum = int(checksum_str.strip(), 16)
        computed = 0
        for ch in data:
            computed ^= ord(ch)
        return computed == checksum
    except (ValueError, IndexError):
        return False


def _parse_lat(lat_str: str, hemi: str) -> float | None:
    """Convert NMEA latitude string to decimal degrees."""
    if not lat_str or not hemi:
        return None
    try:
        # NMEA format: DDMM.MMMM
        degrees = int(float(lat_str) / 100)
        minutes = float(lat_str) - degrees * 100
        decimal = degrees + minutes / 60.0
        if hemi.upper() == "S":
            decimal = -decimal
        return decimal
    except (ValueError, TypeError):
        return None


def _parse_lon(lon_str: str, hemi: str) -> float | None:
    """Convert NMEA longitude string to decimal degrees."""
    if not lon_str or not hemi:
        return None
    try:
        # NMEA format: DDDMM.MMMM
        degrees = int(float(lon_str) / 100)
        minutes = float(lon_str) - degrees * 100
        decimal = degrees + minutes / 60.0
        if hemi.upper() == "W":
            decimal = -decimal
        return decimal
    except (ValueError, TypeError):
        return None


def _parse_gprmc(fields: list[str]) -> dict:
    """
    Parse $GPRMC sentence fields (after sentence type).
    Fields: time, status, lat, N/S, lon, E/W, speed_knots, track, date, ...
    Returns partial fix dict or empty dict if invalid.
    """
    # Minimum required fields
    if len(fields) < 8:
        return {}

    status = fields[1].upper()
    if status != "A":
        # No valid fix
        return {"valid": False}

    lat = _parse_lat(fields[2], fields[3])
    lon = _parse_lon(fields[4], fields[5])

    if lat is None or lon is None:
        return {"valid": False}

    try:
        speed_knots = float(fields[6]) if fields[6] else 0.0
        speed_kmh = speed_knots * 1.852
    except ValueError:
        speed_kmh = 0.0

    # Build UTC timestamp from HHMMSS.ss and DDMMYY
    timestamp = None
    try:
        time_str = fields[0]  # HHMMSS.ss
        date_str = fields[8] if len(fields) > 8 else ""
        if len(time_str) >= 6 and len(date_str) == 6:
            hh = time_str[0:2]
            mm = time_str[2:4]
            ss = time_str[4:6]
            dd = date_str[0:2]
            mo = date_str[2:4]
            yy = date_str[4:6]
            timestamp = f"20{yy}-{mo}-{dd}T{hh}:{mm}:{ss}Z"
    except (IndexError, ValueError):
        pass

    return {
        "valid": True,
        "lat": lat,
        "lon": lon,
        "speed_kmh": round(speed_kmh, 2),
        "timestamp": timestamp,
    }


def _parse_gpgga(fields: list[str]) -> dict:
    """
    Parse $GPGGA sentence fields (after sentence type).
    Fields: time, lat, N/S, lon, E/W, fix_quality, num_sats, hdop, alt, alt_unit, ...
    Returns partial fix dict or empty dict.
    """
    if len(fields) < 9:
        return {}

    try:
        fix_quality = int(fields[5]) if fields[5] else 0
    except ValueError:
        fix_quality = 0

    try:
        satellites = int(fields[6]) if fields[6] else 0
    except ValueError:
        satellites = 0

    try:
        hdop = float(fields[7]) if fields[7] else 99.9
    except ValueError:
        hdop = 99.9

    try:
        altitude_m = float(fields[8]) if fields[8] else 0.0
    except ValueError:
        altitude_m = 0.0

    return {
        "fix_quality": fix_quality,
        "satellites": satellites,
        "hdop": round(hdop, 2),
        "altitude_m": round(altitude_m, 1),
    }


def _reader_loop(port: str, baud: int) -> None:
    """Main reader loop. Runs in background thread."""
    global _running

    while _running:
        try:
            logger.info(f"GPS: connecting to {port} at {baud} baud")
            with serial.Serial(port, baud, timeout=2.0) as ser:
                logger.info("GPS: serial port opened")
                while _running:
                    try:
                        raw = ser.readline()
                    except serial.SerialException as e:
                        logger.warning(f"GPS: serial read error: {e}")
                        break

                    if not raw:
                        continue

                    try:
                        line = raw.decode("ascii", errors="replace").strip()
                    except Exception:
                        continue

                    if not line.startswith("$"):
                        continue

                    # Strip leading $
                    sentence = line[1:]

                    # Validate checksum
                    if not _validate_checksum(sentence):
                        continue

                    # Strip checksum suffix for parsing
                    if "*" in sentence:
                        sentence_body = sentence.rsplit("*", 1)[0]
                    else:
                        sentence_body = sentence

                    fields = sentence_body.split(",")
                    sentence_type = fields[0].upper()
                    data_fields = fields[1:]

                    update = {}
                    if sentence_type in ("GPRMC", "GNRMC"):
                        update = _parse_gprmc(data_fields)
                    elif sentence_type in ("GPGGA", "GNGGA"):
                        update = _parse_gpgga(data_fields)

                    if update:
                        with _lock:
                            _current_fix.update(update)

        except serial.SerialException as e:
            logger.warning(f"GPS: could not open {port}: {e}. Retrying in 5s...")
        except Exception as e:
            logger.exception(f"GPS: unexpected error: {e}")

        if _running:
            time.sleep(5)


def start(port: str = "/dev/serial0", baud: int = 9600) -> None:
    """Start the GPS reader background thread."""
    global _running, _thread
    if _thread and _thread.is_alive():
        logger.warning("GPS: thread already running")
        return
    _running = True
    _thread = threading.Thread(target=_reader_loop, args=(port, baud), daemon=True, name="gps-reader")
    _thread.start()
    logger.info(f"GPS: reader thread started on {port}")


def stop() -> None:
    """Stop the GPS reader background thread."""
    global _running
    _running = False
    logger.info("GPS: reader thread stop requested")


def get_fix() -> dict:
    """Return a copy of the current GPS fix dict."""
    with _lock:
        return dict(_current_fix)
