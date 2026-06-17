"""
session.py - Manages a single spreader run (GPS track + coverage).

Incrementally unions swath polygons, detects overlap, logs to CSV,
and exports track/coverage GeoJSON.
"""

import csv
import json
import os
import time
import logging
from datetime import datetime, timezone
from typing import Optional

from shapely.geometry import LineString, mapping
from shapely.ops import unary_union

from geometry import (
    feet_to_meters,
    buffer_track,
    compute_overlap_fraction,
    clip_to_boundary,
    polygon_area_sqft,
    shape_to_geojson,
    clean_coverage,
    distance_m,
)

logger = logging.getLogger(__name__)

# Minimum ground movement (metres) between two spreading fixes before we draw
# a new swath segment. Filters out GPS jitter while standing still / very slow.
MIN_MOVE_M = 0.5


class Session:
    """
    Tracks a single spreader session.

    Parameters
    ----------
    spread_width_ft : float
        Physical spread width of the spreader in feet.
    pass_spacing_ft : float
        Target spacing between passes in feet (used for guide lines).
    boundary_poly : Shapely Polygon or None
        Property boundary; coverage is clipped to this if provided.
    log_dir : str
        Directory for CSV and GeoJSON export files.
    """

    def __init__(
        self,
        spread_width_ft: float,
        pass_spacing_ft: float,
        boundary_poly,
        log_dir: str,
    ):
        self.spread_width_ft = spread_width_ft
        self.pass_spacing_ft = pass_spacing_ft
        self.boundary_poly = boundary_poly
        self.log_dir = log_dir

        os.makedirs(log_dir, exist_ok=True)

        self.start_time = time.time()
        self.start_dt = datetime.now(timezone.utc)
        session_id = self.start_dt.strftime("%Y%m%d_%H%M%S")

        self.csv_path = os.path.join(log_dir, f"session_{session_id}.csv")
        self.geojson_track_path = os.path.join(log_dir, f"track_{session_id}.geojson")
        self.geojson_coverage_path = os.path.join(log_dir, f"coverage_{session_id}.geojson")

        # Open CSV in append mode so data survives unexpected shutdown
        self._csv_file = open(self.csv_path, "a", newline="")
        self._csv_writer = csv.writer(self._csv_file)
        # Write header only if file is new (empty)
        if os.path.getsize(self.csv_path) == 0:
            self._csv_writer.writerow(
                [
                    "timestamp",
                    "lat",
                    "lon",
                    "fix_quality",
                    "satellites",
                    "hdop",
                    "speed_kmh",
                    "width_ft",
                    "spreading",
                ]
            )
            self._csv_file.flush()

        # State
        self.fixes: list[dict] = []          # All fixes received
        self.covered_union = None            # Shapely geometry: union of all swaths
        self.prev_spreading_fix: Optional[dict] = None  # Last fix where spreading=True
        self.total_fixes = 0
        self.spreading_fixes = 0
        self.overlap_detected = False

        # Last computed values for get_stats()
        self._last_overlap_fraction = 0.0
        self._last_swath_geojson = None

    # ------------------------------------------------------------------
    # Core update method
    # ------------------------------------------------------------------

    def add_fix(self, fix_dict: dict, spreading: bool) -> dict:
        """
        Process a new GPS fix.

        If spreading=True and we have a prior spreading fix, compute a new
        swath polygon, union it with existing coverage, and check overlap.

        Always writes to CSV.

        Returns
        -------
        dict with keys:
            new_swath_geojson : GeoJSON geometry dict or None
            overlap_fraction  : float 0..1
        """
        if not fix_dict.get("valid", False):
            return {"new_swath_geojson": None, "overlap_fraction": 0.0}

        self.fixes.append(fix_dict)
        self.total_fixes += 1

        # Write CSV row
        try:
            self._csv_writer.writerow(
                [
                    fix_dict.get("timestamp", ""),
                    fix_dict.get("lat", ""),
                    fix_dict.get("lon", ""),
                    fix_dict.get("fix_quality", 0),
                    fix_dict.get("satellites", 0),
                    fix_dict.get("hdop", 99.9),
                    fix_dict.get("speed_kmh", 0.0),
                    self.spread_width_ft,
                    1 if spreading else 0,
                ]
            )
            self._csv_file.flush()
        except Exception as e:
            logger.warning(f"session: CSV write failed: {e}")

        new_swath_poly = None
        overlap_fraction = 0.0

        if spreading:
            self.spreading_fixes += 1

            if self.prev_spreading_fix is None:
                # First spreading fix — set the anchor, nothing to draw yet
                self.prev_spreading_fix = fix_dict
            else:
                prev = self.prev_spreading_fix
                curr = fix_dict
                moved = distance_m(prev["lon"], prev["lat"], curr["lon"], curr["lat"])

                if moved < MIN_MOVE_M:
                    # Too little movement — likely GPS jitter. Keep the anchor
                    # so we measure from the last real position, draw nothing.
                    pass
                else:
                    coords = [
                        (prev["lon"], prev["lat"]),
                        (curr["lon"], curr["lat"]),
                    ]
                    width_m = feet_to_meters(self.spread_width_ft)
                    new_swath_poly = buffer_track(coords, width_m)

                    if new_swath_poly is not None and not new_swath_poly.is_empty:
                        # Check overlap before unioning
                        overlap_fraction = compute_overlap_fraction(
                            new_swath_poly, self.covered_union
                        )
                        if overlap_fraction > 0.05:
                            self.overlap_detected = True

                        # Clip to boundary if available
                        clipped = clip_to_boundary(new_swath_poly, self.boundary_poly)

                        # Union into covered area
                        if self.covered_union is None:
                            self.covered_union = clipped
                        else:
                            try:
                                self.covered_union = unary_union(
                                    [self.covered_union, clipped]
                                )
                            except Exception as e:
                                logger.warning(f"session: union failed: {e}")

                    # Advance the anchor only when we actually moved
                    self.prev_spreading_fix = fix_dict
        else:
            # Gap in spreading resets continuity
            self.prev_spreading_fix = None

        self._last_overlap_fraction = overlap_fraction
        if new_swath_poly is not None:
            self._last_swath_geojson = shape_to_geojson(new_swath_poly)
        else:
            self._last_swath_geojson = None

        return {
            "new_swath_geojson": self._last_swath_geojson,
            "overlap_fraction": overlap_fraction,
        }

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return session statistics."""
        duration = time.time() - self.start_time
        area_sqft = 0.0
        pct_coverage = None

        if self.covered_union is not None and not self.covered_union.is_empty:
            area_sqft = polygon_area_sqft(self.covered_union)

        if self.boundary_poly is not None:
            boundary_sqft = polygon_area_sqft(self.boundary_poly)
            if boundary_sqft > 0:
                pct_coverage = round(min(100.0, (area_sqft / boundary_sqft) * 100), 1)

        return {
            "area_sqft": round(area_sqft, 1),
            "area_acres": round(area_sqft / 43560, 4),
            "pct_coverage": pct_coverage,
            "duration_seconds": round(duration, 1),
            "total_fixes": self.total_fixes,
            "spreading_fixes": self.spreading_fixes,
            "overlap_detected": self.overlap_detected,
        }

    # ------------------------------------------------------------------
    # GeoJSON access
    # ------------------------------------------------------------------

    def get_coverage_geojson(self) -> Optional[dict]:
        """Return simplified GeoJSON of covered area, or None."""
        if self.covered_union is None or self.covered_union.is_empty:
            return None
        try:
            cleaned = clean_coverage(self.covered_union)
            simplified = cleaned.simplify(0.000005)
            return {
                "type": "Feature",
                "geometry": mapping(simplified),
                "properties": {"type": "coverage"},
            }
        except Exception as e:
            logger.warning(f"session: get_coverage_geojson failed: {e}")
            return None

    def get_track_geojson(self) -> Optional[dict]:
        """Return GeoJSON LineString of all GPS fixes, or None."""
        valid_fixes = [f for f in self.fixes if f.get("valid")]
        if len(valid_fixes) < 2:
            return None
        try:
            coords = [(f["lon"], f["lat"]) for f in valid_fixes]
            line = LineString(coords)
            return {
                "type": "Feature",
                "geometry": mapping(line),
                "properties": {"type": "track"},
            }
        except Exception as e:
            logger.warning(f"session: get_track_geojson failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Export methods
    # ------------------------------------------------------------------

    def export_csv(self) -> str:
        """Flush and return path to the CSV log file."""
        try:
            self._csv_file.flush()
        except Exception:
            pass
        return self.csv_path

    def export_geojson_track(self) -> str:
        """Save track GeoJSON and return its path."""
        track = self.get_track_geojson()
        fc = {
            "type": "FeatureCollection",
            "features": [track] if track else [],
        }
        with open(self.geojson_track_path, "w") as f:
            json.dump(fc, f, indent=2)
        return self.geojson_track_path

    def export_geojson_coverage(self) -> str:
        """Save coverage GeoJSON and return its path."""
        coverage = self.get_coverage_geojson()
        fc = {
            "type": "FeatureCollection",
            "features": [coverage] if coverage else [],
        }
        with open(self.geojson_coverage_path, "w") as f:
            json.dump(fc, f, indent=2)
        return self.geojson_coverage_path

    def close(self) -> None:
        """Close file handles. Call when session ends."""
        try:
            self._csv_file.close()
        except Exception:
            pass
