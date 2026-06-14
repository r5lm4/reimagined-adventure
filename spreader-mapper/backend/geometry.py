"""
geometry.py - GIS operations using pyproj and Shapely.

All buffering is performed in local UTM projections, never in degrees.
"""

import math
import json
import logging
from typing import Optional

from pyproj import Transformer
from shapely.geometry import (
    LineString,
    Point,
    Polygon,
    MultiPolygon,
    shape,
    mapping,
)
from shapely.ops import transform, unary_union

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Coordinate reference systems
# ---------------------------------------------------------------------------

def _utm_epsg(lon: float, lat: float) -> int:
    """Return the EPSG code for the appropriate UTM zone."""
    zone = int((lon + 180) / 6) + 1
    if lat >= 0:
        return 32600 + zone  # WGS 84 / UTM zone NN N
    else:
        return 32700 + zone  # WGS 84 / UTM zone NN S


def get_utm_transformers(lat: float, lon: float):
    """
    Return (to_utm, to_wgs84) Transformer objects for the UTM zone at (lat, lon).
    always_xy=True means (lon, lat) order for geographic CRS.
    """
    epsg = _utm_epsg(lon, lat)
    to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    to_wgs84 = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    return to_utm, to_wgs84


# ---------------------------------------------------------------------------
# Unit conversions
# ---------------------------------------------------------------------------

def feet_to_meters(ft: float) -> float:
    """Convert feet to meters."""
    return ft * 0.3048


# ---------------------------------------------------------------------------
# Buffering
# ---------------------------------------------------------------------------

def buffer_track(coords_wgs84: list, width_m: float) -> Optional[Polygon]:
    """
    Buffer a track (list of (lon, lat) tuples) by width_m/2 in UTM.

    Returns a Shapely Polygon in WGS84 coordinates, or None on error.
    Handles single-point case by buffering a Point geometry.
    """
    if not coords_wgs84:
        return None

    # Use the first coordinate to choose UTM zone
    lon0, lat0 = coords_wgs84[0]
    to_utm, to_wgs84 = get_utm_transformers(lat0, lon0)

    def _project(geom):
        return transform(to_utm.transform, geom)

    def _unproject(geom):
        return transform(to_wgs84.transform, geom)

    try:
        if len(coords_wgs84) == 1:
            geom_wgs84 = Point(coords_wgs84[0])
        else:
            geom_wgs84 = LineString(coords_wgs84)

        geom_utm = _project(geom_wgs84)
        buffered_utm = geom_utm.buffer(width_m / 2.0, cap_style=2, join_style=2)
        return _unproject(buffered_utm)
    except Exception as e:
        logger.exception(f"geometry: buffer_track failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Overlap detection
# ---------------------------------------------------------------------------

def compute_overlap_fraction(new_swath_poly, existing_union_poly) -> float:
    """
    Return the fraction (0..1) of new_swath_poly already covered by existing_union_poly.
    Returns 0.0 if existing_union_poly is None or new_swath_poly has no area.
    """
    if existing_union_poly is None or new_swath_poly is None:
        return 0.0
    try:
        new_area = new_swath_poly.area
        if new_area == 0:
            return 0.0
        intersection = new_swath_poly.intersection(existing_union_poly)
        return min(1.0, intersection.area / new_area)
    except Exception as e:
        logger.warning(f"geometry: overlap computation failed: {e}")
        return 0.0


# ---------------------------------------------------------------------------
# Guide lines
# ---------------------------------------------------------------------------

def generate_guide_lines(
    boundary_geojson_dict: Optional[dict],
    spacing_m: float,
    bearing_deg: float = 0,
) -> dict:
    """
    Generate N-S parallel guide lines spaced spacing_m apart, clipped to boundary.

    boundary_geojson_dict: GeoJSON dict (FeatureCollection or Feature or Polygon geometry)
    spacing_m: line spacing in meters
    bearing_deg: reserved for future direction control (0 = N-S lines)

    Returns a GeoJSON FeatureCollection of LineString features.
    """
    empty_fc = {"type": "FeatureCollection", "features": []}

    if not boundary_geojson_dict:
        return empty_fc

    boundary_poly = _geojson_to_shape(boundary_geojson_dict)
    if boundary_poly is None or boundary_poly.is_empty:
        return empty_fc

    try:
        minlon, minlat, maxlon, maxlat = boundary_poly.bounds

        # Center point for UTM selection
        clat = (minlat + maxlat) / 2
        clon = (minlon + maxlon) / 2
        to_utm, to_wgs84 = get_utm_transformers(clat, clon)

        # Project boundary to UTM
        boundary_utm = transform(to_utm.transform, boundary_poly)
        minx, miny, maxx, maxy = boundary_utm.bounds

        # Generate vertical lines (N-S) at spacing_m intervals
        features = []
        x = minx
        while x <= maxx + spacing_m:
            line_utm = LineString([(x, miny - 1), (x, maxy + 1)])
            clipped_utm = line_utm.intersection(boundary_utm)
            if clipped_utm.is_empty:
                x += spacing_m
                continue
            # Handle MultiLineString results
            geoms = (
                list(clipped_utm.geoms)
                if hasattr(clipped_utm, "geoms")
                else [clipped_utm]
            )
            for geom in geoms:
                if geom.is_empty:
                    continue
                geom_wgs84 = transform(to_wgs84.transform, geom)
                features.append(
                    {
                        "type": "Feature",
                        "geometry": mapping(geom_wgs84),
                        "properties": {},
                    }
                )
            x += spacing_m

        return {"type": "FeatureCollection", "features": features}

    except Exception as e:
        logger.exception(f"geometry: generate_guide_lines failed: {e}")
        return empty_fc


# ---------------------------------------------------------------------------
# Area calculation
# ---------------------------------------------------------------------------

def polygon_area_sqft(poly_wgs84) -> float:
    """Return area of a WGS84 Shapely polygon in square feet."""
    if poly_wgs84 is None or poly_wgs84.is_empty:
        return 0.0
    try:
        bounds = poly_wgs84.bounds
        clat = (bounds[1] + bounds[3]) / 2
        clon = (bounds[0] + bounds[2]) / 2
        to_utm, _ = get_utm_transformers(clat, clon)
        poly_utm = transform(to_utm.transform, poly_wgs84)
        area_m2 = poly_utm.area
        # 1 m² = 10.7639 ft²
        return area_m2 * 10.7639
    except Exception as e:
        logger.warning(f"geometry: polygon_area_sqft failed: {e}")
        return 0.0


# ---------------------------------------------------------------------------
# Boundary clipping
# ---------------------------------------------------------------------------

def clip_to_boundary(poly, boundary_poly):
    """
    Intersect poly with boundary_poly.
    Returns poly unchanged if boundary_poly is None.
    """
    if boundary_poly is None or poly is None:
        return poly
    try:
        result = poly.intersection(boundary_poly)
        return result if not result.is_empty else poly
    except Exception as e:
        logger.warning(f"geometry: clip_to_boundary failed: {e}")
        return poly


# ---------------------------------------------------------------------------
# GeoJSON helpers
# ---------------------------------------------------------------------------

def shape_to_geojson(shapely_geom) -> dict:
    """Convert a Shapely geometry to a GeoJSON geometry dict."""
    if shapely_geom is None:
        return None
    return mapping(shapely_geom)


def _geojson_to_shape(geojson_dict: dict):
    """Convert a GeoJSON dict (FeatureCollection, Feature, or geometry) to Shapely shape."""
    if geojson_dict is None:
        return None
    try:
        gtype = geojson_dict.get("type")
        if gtype == "FeatureCollection":
            features = geojson_dict.get("features", [])
            if not features:
                return None
            geoms = [shape(f["geometry"]) for f in features if f.get("geometry")]
            if not geoms:
                return None
            return unary_union(geoms)
        elif gtype == "Feature":
            return shape(geojson_dict["geometry"])
        else:
            # Assume it's a geometry object directly
            return shape(geojson_dict)
    except Exception as e:
        logger.warning(f"geometry: _geojson_to_shape failed: {e}")
        return None


def load_boundary_geojson(path: str) -> Optional[Polygon]:
    """
    Load a GeoJSON file and return a Shapely Polygon (or MultiPolygon), or None.
    """
    try:
        with open(path, "r") as f:
            data = json.load(f)
        poly = _geojson_to_shape(data)
        if poly is None:
            logger.warning(f"geometry: could not parse boundary from {path}")
        else:
            logger.info(f"geometry: loaded boundary from {path} ({poly.geom_type})")
        return poly
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.exception(f"geometry: load_boundary_geojson({path}) failed: {e}")
        return None
