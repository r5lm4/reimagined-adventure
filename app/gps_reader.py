"""
gps_reader.py
Reads CSV files written by the Arduino SD card logger.
The Arduino writes: timestamp,lat,lon,speed_kmh
"""

import csv
from pathlib import Path


def load_csv(path: str) -> list[dict]:
    """
    Load a LOG*.CSV file from the SD card.
    Returns a list of fix dicts with keys: timestamp, lat, lon, speed_kmh.
    Skips malformed rows silently.
    """
    fixes = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                fixes.append({
                    "timestamp": row["timestamp"].strip(),
                    "lat":       float(row["lat"]),
                    "lon":       float(row["lon"]),
                    "speed_kmh": float(row["speed_kmh"]),
                })
            except (KeyError, ValueError):
                continue
    return fixes


def load_all_csvs(directory: str) -> dict[str, list[dict]]:
    """
    Load every LOG*.CSV file in a directory (e.g. the mounted SD card).
    Returns {filename: [fixes]} so the caller can pick a specific session
    or merge them all.
    """
    sessions = {}
    p = Path(directory)
    for f in sorted(p.glob("LOG*.CSV")):
        fixes = load_csv(str(f))
        if fixes:
            sessions[f.name] = fixes
            print(f"  {f.name}: {len(fixes)} fixes")
    return sessions
