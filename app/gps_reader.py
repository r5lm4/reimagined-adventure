"""
gps_reader.py
Reads $LOG records from the Arduino over USB serial and writes them to a CSV.
Can also replay an existing CSV for testing without hardware.
"""

import csv
import time
import datetime
from pathlib import Path
import serial


LOG_HEADER = ["timestamp", "lat", "lon", "speed_kmh"]


def stream_from_serial(port: str, baud: int = 115200):
    """Yield parsed fix dicts from a live Arduino connection."""
    with serial.Serial(port, baud, timeout=1) as ser:
        print(f"Connected to {port} at {baud} baud. Sending START command...")
        time.sleep(2)          # wait for Arduino reset
        ser.write(b"S\n")
        print("Logging started. Press Ctrl+C to stop.\n")
        try:
            while True:
                line = ser.readline().decode("ascii", errors="ignore").strip()
                fix = _parse_line(line)
                if fix:
                    yield fix
        except KeyboardInterrupt:
            ser.write(b"P\n")
            print("\nLogging paused.")


def replay_csv(path: str):
    """Yield fix dicts from a saved CSV (for offline testing / re-mapping)."""
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            yield {
                "timestamp": row["timestamp"],
                "lat":       float(row["lat"]),
                "lon":       float(row["lon"]),
                "speed_kmh": float(row["speed_kmh"]),
            }


def record_session(port: str, out_csv: str, baud: int = 115200):
    """Stream from serial and write every fix to out_csv. Returns list of fixes."""
    out = Path(out_csv)
    fixes = []
    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_HEADER)
        writer.writeheader()
        for fix in stream_from_serial(port, baud):
            writer.writerow(fix)
            f.flush()
            fixes.append(fix)
            print(f"  {fix['timestamp']}  {fix['lat']:.6f}, {fix['lon']:.6f}  "
                  f"{fix['speed_kmh']:.1f} km/h")
    print(f"\nSaved {len(fixes)} fixes → {out}")
    return fixes


def load_csv(path: str):
    return list(replay_csv(path))


# ── internal ──────────────────────────────────────────────────────────────────

def _parse_line(line: str):
    if not line.startswith("$LOG,"):
        return None
    parts = line.split(",")
    if len(parts) < 5:
        return None
    try:
        return {
            "timestamp": parts[1],
            "lat":       float(parts[2]),
            "lon":       float(parts[3]),
            "speed_kmh": float(parts[4]),
        }
    except ValueError:
        return None
