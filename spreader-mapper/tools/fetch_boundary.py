#!/usr/bin/env python3
"""
fetch_boundary.py - Fetch property boundary GeoJSON from public sources.

Usage:
    python fetch_boundary.py --address "123 Main St, City, ST" --out boundary.geojson
    python fetch_boundary.py --lat 30.123 --lon -93.456 --out boundary.geojson
    python fetch_boundary.py --list-files --out /path/to/boundaries/

Sources tried in order:
    1. Regrid API (if REGRID_API_KEY env var is set)
    2. OpenStreetMap Overpass API (free, no key)
"""

import argparse
import json
import os
import sys
from pathlib import Path

import requests
from shapely.geometry import shape, mapping, Polygon, MultiPolygon
from shapely.ops import unary_union


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------

def geocode_address(address: str) -> tuple[float, float]:
    """
    Convert address string to (lat, lon) using Nominatim.
    Returns (lat, lon) or raises RuntimeError.
    """
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": address,
        "format": "json",
        "limit": 1,
    }
    headers = {"User-Agent": "spreader-mapper/1.0 (boundary fetch tool)"}
    resp = requests.get(url, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise RuntimeError(f"Address not found: {address!r}")
    lat = float(results[0]["lat"])
    lon = float(results[0]["lon"])
    print(f"Geocoded: {address!r} → ({lat:.6f}, {lon:.6f})")
    return lat, lon


# ---------------------------------------------------------------------------
# Source 1: Regrid API
# ---------------------------------------------------------------------------

def fetch_regrid(lat: float, lon: float, api_key: str) -> dict | None:
    """
    Fetch parcel boundary from Regrid API.
    Returns GeoJSON Feature dict or None.
    """
    url = "https://app.regrid.com/api/v1/parcel"
    params = {
        "lat": lat,
        "lon": lon,
        "token": api_key,
        "return_geometry": "true",
        "return_custom": "false",
    }
    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        # Regrid returns a FeatureCollection
        features = data.get("parcels", {}).get("features", [])
        if not features:
            print("Regrid: no parcel found at this location")
            return None
        feature = features[0]
        print(f"Regrid: found parcel ({feature.get('properties', {}).get('ll_gisacre', '?')} acres)")
        return feature
    except requests.RequestException as e:
        print(f"Regrid: request failed: {e}")
        return None
    except Exception as e:
        print(f"Regrid: error: {e}")
        return None


# ---------------------------------------------------------------------------
# Source 2: OpenStreetMap Overpass API
# ---------------------------------------------------------------------------

def fetch_overpass(lat: float, lon: float) -> dict | None:
    """
    Fetch building/landuse polygon from Overpass API near (lat, lon).
    Tries residential building first, then landuse=residential area.
    Returns GeoJSON Feature dict or None.
    """
    overpass_url = "https://overpass-api.de/api/interpreter"

    # Search radius in meters
    radius = 30

    # Query for a building or residential lot at this point
    query = f"""
[out:json][timeout:25];
(
  way(around:{radius},{lat},{lon})[building];
  way(around:{radius},{lat},{lon})[landuse=residential];
  relation(around:{radius},{lat},{lon})[building];
);
out body;
>;
out skel qt;
"""
    headers = {"User-Agent": "spreader-mapper/1.0 (boundary fetch tool)"}
    try:
        print(f"Overpass: querying for structures within {radius}m of ({lat:.6f}, {lon:.6f})...")
        resp = requests.post(overpass_url, data={"data": query}, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        elements = data.get("elements", [])

        if not elements:
            # Widen search
            print("Overpass: no results in 30m, widening to 100m...")
            query2 = f"""
[out:json][timeout:25];
(
  way(around:100,{lat},{lon})[building];
  way(around:100,{lat},{lon})[landuse=residential];
  way(around:100,{lat},{lon})[landuse=grass];
);
out body;
>;
out skel qt;
"""
            resp = requests.post(overpass_url, data={"data": query2}, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            elements = data.get("elements", [])

        if not elements:
            print("Overpass: no polygon features found")
            return None

        # Build node index
        nodes = {el["id"]: el for el in elements if el["type"] == "node"}
        ways = [el for el in elements if el["type"] == "way"]

        if not ways:
            print("Overpass: no way elements found")
            return None

        # Use the first way that forms a closed polygon
        for way in ways:
            node_ids = way.get("nodes", [])
            if len(node_ids) < 4:
                continue
            if node_ids[0] != node_ids[-1]:
                continue  # Not closed
            coords = []
            for nid in node_ids:
                if nid in nodes:
                    n = nodes[nid]
                    coords.append((n["lon"], n["lat"]))
            if len(coords) < 4:
                continue
            tags = way.get("tags", {})
            feature = {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [coords],
                },
                "properties": tags,
            }
            print(f"Overpass: found {tags.get('building', tags.get('landuse', 'polygon'))} way ID {way['id']}")
            return feature

        print("Overpass: could not build valid polygon from results")
        return None

    except requests.RequestException as e:
        print(f"Overpass: request failed: {e}")
        return None
    except Exception as e:
        print(f"Overpass: error: {e}")
        return None


# ---------------------------------------------------------------------------
# Polygon simplification
# ---------------------------------------------------------------------------

def simplify_polygon(feature: dict, max_points: int = 50) -> dict:
    """
    Simplify a GeoJSON Feature polygon to at most max_points vertices
    using Douglas-Peucker algorithm via Shapely.
    """
    geom = shape(feature["geometry"])

    # Convert MultiPolygon to largest polygon
    if isinstance(geom, MultiPolygon):
        geom = max(geom.geoms, key=lambda g: g.area)

    if not isinstance(geom, Polygon):
        return feature

    original_count = len(geom.exterior.coords)
    if original_count <= max_points:
        feature["geometry"] = mapping(geom)
        return feature

    # Incrementally increase tolerance until we're under max_points
    tolerance = 0.000001
    simplified = geom
    for _ in range(30):
        candidate = geom.simplify(tolerance, preserve_topology=True)
        if candidate.is_valid and not candidate.is_empty:
            simplified = candidate
            if len(simplified.exterior.coords) <= max_points:
                break
        tolerance *= 2

    print(f"Simplified: {original_count} → {len(simplified.exterior.coords)} points")
    feature["geometry"] = mapping(simplified)
    return feature


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def compute_info(feature: dict) -> dict:
    """Return bbox, area in acres, and point count for a feature."""
    geom = shape(feature["geometry"])
    bounds = geom.bounds  # (minlon, minlat, maxlon, maxlat)
    coords = list(geom.exterior.coords) if isinstance(geom, Polygon) else []

    # Approximate area in acres using a local UTM projection
    try:
        from pyproj import Transformer
        from shapely.ops import transform as shapely_transform

        clat = (bounds[1] + bounds[3]) / 2
        clon = (bounds[0] + bounds[2]) / 2
        zone = int((clon + 180) / 6) + 1
        epsg = 32600 + zone if clat >= 0 else 32700 + zone
        to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
        geom_utm = shapely_transform(to_utm.transform, geom)
        area_m2 = geom_utm.area
        area_acres = area_m2 / 4046.856
    except Exception:
        area_acres = 0.0

    return {
        "bbox": [round(b, 6) for b in bounds],
        "area_acres": round(area_acres, 3),
        "point_count": len(coords),
    }


def save_geojson(feature: dict, out_path: str) -> None:
    """Save feature as a GeoJSON FeatureCollection."""
    fc = {
        "type": "FeatureCollection",
        "features": [feature],
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(fc, f, indent=2)
    print(f"Saved: {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Fetch property boundary GeoJSON for the spreader mapper."
    )
    parser.add_argument("--address", help="Street address to look up")
    parser.add_argument("--lat", type=float, help="Latitude (instead of address)")
    parser.add_argument("--lon", type=float, help="Longitude (instead of address)")
    parser.add_argument(
        "--out",
        default="boundary.geojson",
        help="Output file path (default: boundary.geojson)",
    )
    parser.add_argument(
        "--list-files",
        action="store_true",
        help="List available .geojson files in the output directory and exit",
    )
    parser.add_argument(
        "--max-points",
        type=int,
        default=50,
        help="Maximum polygon vertices after simplification (default: 50)",
    )
    args = parser.parse_args()

    # List mode
    if args.list_files:
        out_dir = Path(args.out) if Path(args.out).is_dir() else Path(args.out).parent
        print(f"Boundary files in {out_dir}:")
        files = list(out_dir.glob("*.geojson"))
        if not files:
            print("  (none found)")
        else:
            for f in sorted(files):
                size_kb = f.stat().st_size / 1024
                print(f"  {f.name}  ({size_kb:.1f} KB)")
        return

    # Resolve coordinates
    if args.address:
        lat, lon = geocode_address(args.address)
    elif args.lat is not None and args.lon is not None:
        lat, lon = args.lat, args.lon
    else:
        parser.error("Provide --address or --lat/--lon")

    print(f"\nFetching boundary for ({lat:.6f}, {lon:.6f})...")

    feature = None

    # Try Regrid first
    regrid_key = os.environ.get("REGRID_API_KEY")
    if regrid_key:
        print("\n[1/2] Trying Regrid API...")
        feature = fetch_regrid(lat, lon, regrid_key)
    else:
        print("\n[1/2] Skipping Regrid (REGRID_API_KEY not set)")

    # Fall back to Overpass
    if feature is None:
        print("\n[2/2] Trying OpenStreetMap Overpass API...")
        feature = fetch_overpass(lat, lon)

    if feature is None:
        print(
            "\nError: could not fetch boundary from any source.\n"
            "Options:\n"
            "  - Set REGRID_API_KEY environment variable for Regrid access\n"
            "  - Manually draw a boundary at geojson.io and save it\n"
            "  - Ensure the property appears in OpenStreetMap"
        )
        sys.exit(1)

    # Simplify
    print(f"\nSimplifying to max {args.max_points} points...")
    feature = simplify_polygon(feature, max_points=args.max_points)

    # Print info
    info = compute_info(feature)
    print(f"\nBoundary info:")
    print(f"  BBox:        {info['bbox']}")
    print(f"  Area:        {info['area_acres']:.3f} acres")
    print(f"  Vertices:    {info['point_count']}")

    # Save
    save_geojson(feature, args.out)
    print("\nDone! Copy this file to the Pi:")
    print(f"  scp {args.out} pi@192.168.4.1:/home/pi/spreader-mapper/backend/data/boundaries/boundary.geojson")


if __name__ == "__main__":
    main()
