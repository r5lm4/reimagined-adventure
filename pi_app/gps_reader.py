"""
gps_reader.py
Reads NMEA sentences from the L76X GPS on the Pi's UART in a background thread.
Other modules read the latest fix from the shared `current_fix` dict.
"""

import threading
import serial
import time


# Shared state – written by the reader thread, read by the display thread.
current_fix = {
    "valid":     False,
    "lat":       0.0,
    "lon":       0.0,
    "speed_kmh": 0.0,
    "timestamp": "",
}
_lock = threading.Lock()


def get_fix() -> dict:
    """Return a snapshot of the latest GPS fix."""
    with _lock:
        return dict(current_fix)


def start(port: str = "/dev/serial0", baud: int = 9600):
    """Start the GPS reader thread. Call once at startup."""
    t = threading.Thread(target=_reader_loop, args=(port, baud), daemon=True)
    t.start()


# ── internal ──────────────────────────────────────────────────────────────────

def _reader_loop(port: str, baud: int):
    while True:
        try:
            with serial.Serial(port, baud, timeout=1) as ser:
                buf = b""
                while True:
                    buf += ser.read(64)
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        fix = _parse_gprmc(line.decode("ascii", errors="ignore").strip())
                        if fix:
                            with _lock:
                                current_fix.update(fix)
        except serial.SerialException as e:
            print(f"GPS serial error: {e} – retrying in 3 s")
            time.sleep(3)


def _field(parts: list, n: int, default: str = "") -> str:
    return parts[n] if n < len(parts) else default


def _to_deg(raw: str, hemi: str) -> float:
    if not raw:
        return 0.0
    v = float(raw)
    d = int(v / 100)
    dd = d + (v - d * 100) / 60.0
    if hemi in ("S", "W"):
        dd = -dd
    return dd


def _checksum_ok(sentence: str) -> bool:
    if "$" not in sentence or "*" not in sentence:
        return False
    start = sentence.index("$") + 1
    end   = sentence.index("*")
    calc  = 0
    for c in sentence[start:end]:
        calc ^= ord(c)
    try:
        return calc == int(sentence[end + 1:end + 3], 16)
    except ValueError:
        return False


def _parse_gprmc(sentence: str) -> dict | None:
    if not _checksum_ok(sentence):
        return None
    parts = sentence.split(",")
    if parts[0] not in ("$GPRMC", "$GNRMC"):
        return None
    if len(parts) < 10 or parts[2] != "A":
        return None
    try:
        lat = _to_deg(parts[3], parts[4])
        lon = _to_deg(parts[5], parts[6])
        speed_kmh = float(parts[7]) * 1.852 if parts[7] else 0.0
        tf, df = parts[1], parts[9]
        ts = (f"20{df[4:6]}-{df[2:4]}-{df[0:2]}T{tf[0:2]}:{tf[2:4]}:{tf[4:6]}Z"
              if len(tf) >= 6 and len(df) == 6 else "")
        return {"valid": True, "lat": lat, "lon": lon,
                "speed_kmh": speed_kmh, "timestamp": ts}
    except (ValueError, IndexError):
        return None
