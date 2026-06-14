"""
main.py – CLI for the Spreader GPS Mapper

Workflow:
  1. Write the property boundary to the SD card ONCE (before going outside):
       python main.py boundary --address "123 Main St, Anytown, USA" --card E:/

  2. After a session, pull the card and map it:
       python main.py map --csv E:/LOG001.CSV --width 1.5 --address "123 Main St"

  3. Map every session at once:
       python main.py mapall --card E:/ --width 1.5 --address "123 Main St"

  4. Cumulative map across all sessions:
       python main.py merge --card E:/ --width 1.5 --address "123 Main St"
"""

import os
import sys
import csv
import click

from gps_reader        import load_csv, load_all_csvs
from property_boundary import fetch_property_boundary, bounding_box_from_track, simplify_polygon
from coverage_map      import generate_map


@click.group()
def cli():
    """Spreader GPS Mapper – turn SD card CSV logs into coverage maps."""


# ── boundary (write BOUNDARY.CSV to SD card) ──────────────────────────────────

@cli.command()
@click.option("--address", required=True, help="Your street address")
@click.option("--card",    required=True, help="SD card path (e.g. E:/ or /media/sd)")
@click.option("--max-pts", default=24,    show_default=True,
              help="Max boundary points (must match MAX_BOUNDARY_PTS in sketch)")
def boundary(address, card, max_pts):
    """
    Fetch your property boundary and write BOUNDARY.CSV to the SD card.
    Run this once before you go outside. The Arduino reads it on boot.
    """
    # We need a rough centre point to query the API; use geocoding via Nominatim.
    lat, lon = _geocode(address)
    if lat is None:
        click.echo("Could not geocode that address. Try a more complete address.", err=True)
        sys.exit(1)
    click.echo(f"Geocoded to {lat:.6f}, {lon:.6f}")

    poly = fetch_property_boundary(lat, lon, address)
    if poly is None:
        click.echo("Could not find a property boundary. Check your address or set REGRID_API_KEY.", err=True)
        sys.exit(1)

    pts = simplify_polygon(poly, max_pts)
    out = os.path.join(card, "BOUNDARY.CSV")
    with open(out, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["lat", "lon"])
        for lon_v, lat_v in pts:   # Shapely exterior coords are (lon, lat)
            writer.writerow([f"{lat_v:.7f}", f"{lon_v:.7f}"])

    click.echo(f"Wrote {len(pts)} boundary points → {out}")
    click.echo("Safely eject the SD card and insert it into the Arduino.")


# ── map (single file) ─────────────────────────────────────────────────────────

@cli.command()
@click.option("--csv",     required=True, help="Path to a LOG*.CSV from the SD card")
@click.option("--width",   default=1.5,   show_default=True, help="Spread width in metres")
@click.option("--address", default=None,  help="Your street address for property boundary lookup")
@click.option("--out",     default="coverage_map.html", show_default=True)
def map(csv, width, address, out):
    """Generate a coverage map from a single SD card CSV file."""
    fixes = load_csv(csv)
    if not fixes:
        click.echo("No valid fixes in that file.", err=True)
        sys.exit(1)
    click.echo(f"Loaded {len(fixes)} fixes from {csv}")
    boundary = _get_boundary(fixes, address)
    path = generate_map(fixes, width, out, property_polygon=boundary)
    click.echo(f"\nOpen in browser: file://{path}")


# ── mapall (one map per session file) ────────────────────────────────────────

@cli.command()
@click.option("--card",    required=True, help="Drive letter or path to mounted SD card (e.g. E:/ or /media/sd)")
@click.option("--width",   default=1.5,   show_default=True, help="Spread width in metres")
@click.option("--address", default=None)
@click.option("--outdir",  default=".",   show_default=True)
def mapall(card, width, address, outdir):
    """Generate a separate coverage map for every LOG*.CSV on the SD card."""
    click.echo(f"Scanning {card} for LOG*.CSV files…")
    sessions = load_all_csvs(card)
    if not sessions:
        click.echo("No LOG*.CSV files found.", err=True)
        sys.exit(1)

    # Fetch boundary once using the first session
    first_fixes = next(iter(sessions.values()))
    boundary = _get_boundary(first_fixes, address)

    os.makedirs(outdir, exist_ok=True)
    for name, fixes in sessions.items():
        stem = os.path.splitext(name)[0]
        out  = os.path.join(outdir, f"{stem}_map.html")
        generate_map(fixes, width, out, property_polygon=boundary, title=f"Session {stem}")
    click.echo(f"\n{len(sessions)} map(s) written to {os.path.abspath(outdir)}")


# ── merge (all sessions → one cumulative map) ─────────────────────────────────

@cli.command()
@click.option("--card",    required=True, help="Drive letter or path to mounted SD card")
@click.option("--width",   default=1.5,   show_default=True, help="Spread width in metres")
@click.option("--address", default=None)
@click.option("--out",     default="cumulative_map.html", show_default=True)
def merge(card, width, address, out):
    """Merge every session on the SD card into one cumulative coverage map."""
    click.echo(f"Scanning {card} for LOG*.CSV files…")
    sessions = load_all_csvs(card)
    if not sessions:
        click.echo("No LOG*.CSV files found.", err=True)
        sys.exit(1)

    all_fixes = [fix for fixes in sessions.values() for fix in fixes]
    click.echo(f"Merged {len(all_fixes)} fixes from {len(sessions)} session(s)")

    boundary = _get_boundary(all_fixes, address)
    path = generate_map(all_fixes, width, out, property_polygon=boundary,
                        title="Cumulative Coverage")
    click.echo(f"\nOpen in browser: file://{path}")


# ── helpers ───────────────────────────────────────────────────────────────────

def _geocode(address: str):
    """Rough geocode via OSM Nominatim (no key needed). Returns (lat, lon) or (None, None)."""
    import requests
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": address, "format": "json", "limit": 1},
            headers={"User-Agent": "spreader-mapper/1.0"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        click.echo(f"Geocode error: {e}", err=True)
    return None, None


def _get_boundary(fixes, address):
    center_lat = sum(f["lat"] for f in fixes) / len(fixes)
    center_lon = sum(f["lon"] for f in fixes) / len(fixes)
    boundary = fetch_property_boundary(center_lat, center_lon, address)
    if boundary is None:
        click.echo("Using GPS track bounding box as property boundary.")
        boundary = bounding_box_from_track(fixes)
    return boundary


if __name__ == "__main__":
    cli()
