"""
coverage_map.py

Takes a list of GPS fix dicts and a spread width, buffers the path to create
a treated-area polygon, optionally clips it to a property boundary, and
renders an interactive Folium HTML map.
"""

import math
from pathlib import Path

import folium
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union


# Earth radius in metres, used for metre→degree conversion.
_R = 6_371_000


def metres_to_degrees(metres: float, lat: float) -> float:
    """Approximate metre distance to decimal degrees at a given latitude."""
    lat_deg = metres / (_R * math.pi / 180)
    lon_deg = metres / (_R * math.cos(math.radians(lat)) * math.pi / 180)
    return (lat_deg + lon_deg) / 2  # average for circular buffer


def build_treated_area(fixes: list, spread_width_m: float) -> Polygon | None:
    """
    Buffer the GPS track by half the spread width to get treated area.
    Returns a Shapely (Multi)Polygon or None if fewer than 2 fixes.
    """
    if len(fixes) < 2:
        if len(fixes) == 1:
            # Single point – use a circle
            f = fixes[0]
            buf = metres_to_degrees(spread_width_m / 2, f["lat"])
            return Point(f["lon"], f["lat"]).buffer(buf)
        return None

    coords = [(f["lon"], f["lat"]) for f in fixes]
    line   = LineString(coords)

    # Use mean latitude for degree conversion
    mean_lat = sum(f["lat"] for f in fixes) / len(fixes)
    buf_deg  = metres_to_degrees(spread_width_m / 2, mean_lat)

    return line.buffer(buf_deg, cap_style=1, join_style=1)  # round ends


def generate_map(
    fixes: list,
    spread_width_m: float,
    out_html: str,
    property_polygon: Polygon | None = None,
    title: str = "Spreader Coverage Map",
) -> str:
    """
    Build and save an interactive Folium map.

    Layers:
      - Property boundary (blue outline) if provided
      - Treated area (green fill)
      - GPS track (red line)
      - Start / end markers

    Returns the absolute path to the saved HTML file.
    """
    if not fixes:
        raise ValueError("No GPS fixes to map.")

    treated = build_treated_area(fixes, spread_width_m)

    # If we have a property boundary, clip treated area to it
    if property_polygon and treated:
        treated_display = treated.intersection(property_polygon)
    else:
        treated_display = treated

    # Map centre
    center_lat = sum(f["lat"] for f in fixes) / len(fixes)
    center_lon = sum(f["lon"] for f in fixes) / len(fixes)

    m = folium.Map(location=[center_lat, center_lon], zoom_start=19,
                   tiles="OpenStreetMap")

    # ── Property boundary ──────────────────────────────────────────────────
    if property_polygon:
        _add_polygon_layer(m, property_polygon, color="#0055cc", fill=False,
                           weight=3, tooltip="Property boundary",
                           layer_name="Property boundary")

    # ── Treated area ───────────────────────────────────────────────────────
    if treated_display and not treated_display.is_empty:
        _add_polygon_layer(m, treated_display, color="#00aa44", fill=True,
                           fill_color="#00aa44", fill_opacity=0.35, weight=1,
                           tooltip=f"Treated ({spread_width_m:.1f} m spread width)",
                           layer_name="Treated area")

    # ── GPS track ──────────────────────────────────────────────────────────
    track_coords = [[f["lat"], f["lon"]] for f in fixes]
    folium.PolyLine(
        track_coords, color="#cc2200", weight=2, opacity=0.7,
        tooltip="GPS track"
    ).add_to(m)

    # Start marker
    folium.Marker(
        location=[fixes[0]["lat"], fixes[0]["lon"]],
        tooltip=f"Start  {fixes[0]['timestamp']}",
        icon=folium.Icon(color="green", icon="play", prefix="fa"),
    ).add_to(m)

    # End marker
    if len(fixes) > 1:
        folium.Marker(
            location=[fixes[-1]["lat"], fixes[-1]["lon"]],
            tooltip=f"End  {fixes[-1]['timestamp']}",
            icon=folium.Icon(color="red", icon="stop", prefix="fa"),
        ).add_to(m)

    # ── Coverage stats popup ───────────────────────────────────────────────
    stats = _coverage_stats(treated_display, property_polygon)
    folium.Marker(
        location=[center_lat, center_lon],
        tooltip="Click for coverage stats",
        icon=folium.Icon(color="blue", icon="info-sign"),
        popup=folium.Popup(_stats_html(stats, spread_width_m, len(fixes)), max_width=280),
    ).add_to(m)

    folium.LayerControl().add_to(m)

    out_path = str(Path(out_html).resolve())
    m.save(out_path)
    print(f"Map saved → {out_path}")
    return out_path


# ── internal ──────────────────────────────────────────────────────────────────

def _add_polygon_layer(m, geom, layer_name="", **style):
    """Add a Shapely Polygon or MultiPolygon to a Folium map."""
    from shapely.geometry import MultiPolygon as MP
    polys = list(geom.geoms) if isinstance(geom, MP) else [geom]
    fg = folium.FeatureGroup(name=layer_name)
    for poly in polys:
        coords = [[lat, lon] for lon, lat in poly.exterior.coords]
        folium.Polygon(locations=coords, **style).add_to(fg)
    fg.add_to(m)


def _coverage_stats(treated, property_poly):
    stats = {}
    if treated and not treated.is_empty:
        # Convert degree² area to m² (approximate at mid-latitudes)
        stats["treated_m2"] = treated.area * (_R * math.pi / 180) ** 2
    if property_poly and not property_poly.is_empty:
        stats["property_m2"] = property_poly.area * (_R * math.pi / 180) ** 2
        if "treated_m2" in stats:
            stats["pct"] = min(100, stats["treated_m2"] / stats["property_m2"] * 100)
    return stats


def _stats_html(stats, width_m, n_fixes):
    lines = [f"<b>Spread width:</b> {width_m:.1f} m<br>",
             f"<b>GPS fixes:</b> {n_fixes}<br>"]
    if "treated_m2" in stats:
        lines.append(f"<b>Treated area:</b> {stats['treated_m2']:.0f} m² "
                     f"({stats['treated_m2']/10000:.4f} ha)<br>")
    if "property_m2" in stats:
        lines.append(f"<b>Property area:</b> {stats['property_m2']:.0f} m²<br>")
    if "pct" in stats:
        lines.append(f"<b>Coverage:</b> {stats['pct']:.1f}%<br>")
    return "".join(lines)
