"""
property_boundary.py

Fetches a property parcel polygon from one of three sources (tried in order):
  1. Regrid API  – most accurate, requires free API key
  2. OpenStreetMap Overpass – good coverage, no key needed
  3. Synthetic bounding box – fallback from the GPS track itself

Returns a Shapely Polygon in WGS-84 (lat/lon).
"""

import os
import json
import requests
from shapely.geometry import shape, Polygon, MultiPolygon


REGRID_KEY_ENV = "REGRID_API_KEY"
OVERPASS_URL   = "https://overpass-api.de/api/interpreter"
REGRID_URL     = "https://app.regrid.com/api/v2/parcels/point"


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_property_boundary(lat: float, lon: float, address: str | None = None) -> Polygon | None:
    """
    Try each data source in priority order.
    Returns the parcel Polygon or None if every source fails.
    """
    polygon = None

    api_key = os.environ.get(REGRID_KEY_ENV)
    if api_key:
        print("Trying Regrid parcel API…")
        polygon = _from_regrid(lat, lon, api_key)
        if polygon:
            print("  ✓ Regrid returned a parcel boundary.")
            return polygon

    print("Trying OpenStreetMap Overpass…")
    polygon = _from_overpass(lat, lon)
    if polygon:
        print("  ✓ OpenStreetMap returned a boundary.")
        return polygon

    print("  No parcel boundary found online – using GPS track bounding box as fallback.")
    return None


def bounding_box_from_track(fixes: list) -> Polygon:
    """Build a simple rectangular polygon that covers the GPS track with a 10 m margin."""
    lats = [f["lat"] for f in fixes]
    lons = [f["lon"] for f in fixes]
    margin = 0.0001  # ~10 m at mid-latitudes
    return Polygon([
        (min(lons) - margin, min(lats) - margin),
        (max(lons) + margin, min(lats) - margin),
        (max(lons) + margin, max(lats) + margin),
        (min(lons) - margin, max(lats) + margin),
    ])


# ── Regrid ────────────────────────────────────────────────────────────────────

def _from_regrid(lat: float, lon: float, api_key: str) -> Polygon | None:
    try:
        resp = requests.get(
            REGRID_URL,
            params={"lat": lat, "lon": lon, "token": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        features = data.get("parcels", {}).get("features", [])
        if not features:
            return None
        geom = shape(features[0]["geometry"])
        return _to_polygon(geom)
    except Exception as e:
        print(f"  Regrid error: {e}")
        return None


# ── OpenStreetMap Overpass ────────────────────────────────────────────────────

def _from_overpass(lat: float, lon: float) -> Polygon | None:
    """
    Query OSM for a landuse=residential or boundary=land_area way
    that contains the given point, then return its polygon.
    We use a 200 m bounding box around the point as the search area.
    """
    delta = 0.002  # ~200 m
    bbox = f"{lat-delta},{lon-delta},{lat+delta},{lon+delta}"
    # Look for ways/relations tagged as residential plots or similar
    query = f"""
[out:json][timeout:25];
(
  way["landuse"="residential"]({bbox});
  way["boundary"="land_area"]({bbox});
  way["place"="plot"]({bbox});
);
out geom;
"""
    try:
        resp = requests.post(OVERPASS_URL, data={"data": query}, timeout=30)
        resp.raise_for_status()
        elements = resp.json().get("elements", [])
        for el in elements:
            if el.get("type") == "way" and "geometry" in el:
                coords = [(n["lon"], n["lat"]) for n in el["geometry"]]
                if len(coords) >= 3:
                    poly = Polygon(coords)
                    if poly.contains(_point(lon, lat)):
                        return poly
        return None
    except Exception as e:
        print(f"  Overpass error: {e}")
        return None


# ── helpers ───────────────────────────────────────────────────────────────────

def _to_polygon(geom) -> Polygon | None:
    if isinstance(geom, Polygon):
        return geom
    if isinstance(geom, MultiPolygon):
        return max(geom.geoms, key=lambda p: p.area)
    return None


def _point(lon, lat):
    from shapely.geometry import Point
    return Point(lon, lat)
