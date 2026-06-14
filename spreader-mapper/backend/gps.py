"""
gps.py - Thread that reads NMEA sentences from L76X GPS on /dev/serial0.

Parses $GPRMC and $GPGGA sentences, validates checksums, and maintains
a thread-safe shared fix dict. Handles serial disconnects with retry.

Simulation mode
───────────────
Set the environment variable SIM_GPS=1 to run without hardware.
The simulated position walks a small rectangular loop around a configurable
starting coordinate so you can test the full UI on any computer.

    SIM_GPS=1 python app.py
    SIM_GPS=1 SIM_LAT=40.7128 SIM_LON=-74.0060 python app.py
"""

import os
import math
import threading
import time
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


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def _sim_loop(start_lat: float, start_lon: float) -> None:
    """
    Walks a rectangular route ~30 m wide × 60 m long around start_lat/lon.
    Each leg is walked at ~1.5 m/s (slow walking pace), one fix per second.
    The route repeats so you can watch coverage build up indefinitely.
    """
    global _running

    # Approximate degrees per metre at the given latitude
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(start_lat))

    # Rectangular waypoints (offsets in metres from start)
    width_m  = 30.0
    length_m = 60.0
    offsets = [
        (0,        0),
        (length_m, 0),
        (length_m, width_m),
        (0,        width_m),
    ]
    waypoints = [
        (start_lat + dy / m_per_deg_lat, start_lon + dx / m_per_deg_lon)
        for dx, dy in offsets
    ]

    speed_mps   = 1.5   # metres per second
    speed_kmh   = speed_mps * 3.6
    fix_interval = 1.0  # seconds between fixes

    leg     = 0
    frac    = 0.0   # 0..1 progress along current leg
    sats    = 9
    hdop    = 1.1

    logger.info(f"GPS SIM: starting at {start_lat:.6f}, {start_lon:.6f}")

    while _running:
        p1 = waypoints[leg]
        p2 = waypoints[(leg + 1) % len(waypoints)]

        lat = p1[0] + frac * (p2[0] - p1[0])
        lon = p1[1] + frac * (p2[1] - p1[1])

        # Distance of this leg in metres
        dlat_m = (p2[0] - p1[0]) * m_per_deg_lat
        dlon_m = (p2[1] - p1[1]) * m_per_deg_lon
        leg_m  = math.hypot(dlat_m, dlon_m)

        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        with _lock:
            _current_fix.update({
                "valid":       True,
                "lat":         round(lat, 7),
                "lon":         round(lon, 7),
                "speed_kmh":   round(speed_kmh, 2),
                "timestamp":   ts,
                "fix_quality": 1,
                "satellites":  sats,
                "hdop":        hdop,
                "altitude_m":  15.0,
            })

        # Advance along the leg
        if leg_m > 0:
            frac += (speed_mps * fix_interval) / leg_m
        if frac >= 1.0:
            frac = 0.0
            leg  = (leg + 1) % len(waypoints)

        time.sleep(fix_interval)


# ---------------------------------------------------------------------------
# NMEA parsing
# ---------------------------------------------------------------------------

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
        degrees = int(float(lon_str) / 100)
        minutes = float(lon_str) - degrees * 100
        decimal = degrees + minutes / 60.0
        if hemi.upper() == "W":
            decimal = -decimal
        return decimal
    except (ValueError, TypeError):
        return None


def _parse_gprmc(fields: list[str]) -> dict:
    if len(fields) < 8:
        return {}
    status = fields[1].upper()
    if status != "A":
        return {"valid": False}
    lat = _parse_lat(fields[2], fields[3])
    lon = _parse_lon(fields[4], fields[5])
    if lat is None or lon is None:
        return {"valid": False}
    try:
        speed_kmh = float(fields[6]) * 1.852 if fields[6] else 0.0
    except ValueError:
        speed_kmh = 0.0
    timestamp = None
    try:
        time_str = fields[0]
        date_str = fields[8] if len(fields) > 8 else ""
        if len(time_str) >= 6 and len(date_str) == 6:
            timestamp = (f"20{date_str[4:6]}-{date_str[2:4]}-{date_str[0:2]}"
                         f"T{time_str[0:2]}:{time_str[2:4]}:{time_str[4:6]}Z")
    except (IndexError, ValueError):
        pass
    return {"valid": True, "lat": lat, "lon": lon,
            "speed_kmh": round(speed_kmh, 2), "timestamp": timestamp}


def _parse_gpgga(fields: list[str]) -> dict:
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
    return {"fix_quality": fix_quality, "satellites": satellites,
            "hdop": round(hdop, 2), "altitude_m": round(altitude_m, 1)}


# ---------------------------------------------------------------------------
# Serial reader
# ---------------------------------------------------------------------------

def _reader_loop(port: str, baud: int) -> None:
    global _running
    import serial

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
                    sentence = line[1:]
                    if not _validate_checksum(sentence):
                        continue
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start(port: str = "/dev/serial0", baud: int = 9600) -> None:
    """
    Start the GPS background thread.
    If SIM_GPS=1 is set in the environment, runs the simulator instead of
    reading a serial port, so the app can be tested on any computer.
    """
    global _running, _thread
    if _thread and _thread.is_alive():
        logger.warning("GPS: thread already running")
        return
    _running = True

    if os.environ.get("SIM_GPS", "0") == "1":
        sim_lat = float(os.environ.get("SIM_LAT", "30.4515"))
        sim_lon = float(os.environ.get("SIM_LON", "-91.1871"))
        _thread = threading.Thread(
            target=_sim_loop, args=(sim_lat, sim_lon),
            daemon=True, name="gps-sim"
        )
        logger.info(f"GPS: simulation mode ON — centre {sim_lat}, {sim_lon}")
    else:
        _thread = threading.Thread(
            target=_reader_loop, args=(port, baud),
            daemon=True, name="gps-reader"
        )

    _thread.start()


def stop() -> None:
    global _running
    _running = False
    logger.info("GPS: thread stop requested")


def get_fix() -> dict:
    with _lock:
        return dict(_current_fix)
