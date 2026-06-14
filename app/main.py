"""
main.py – CLI for the Spreader GPS Mapper

Usage examples:

  # Record a live session on COM5 (Windows) or /dev/ttyUSB0 (Linux/Mac)
  python main.py record --port COM5 --width 1.5 --out session.csv

  # Generate a map from a previously saved CSV
  python main.py map --csv session.csv --width 1.5 --address "123 Main St, Anytown, USA"

  # Full workflow: record + immediately map
  python main.py run --port /dev/ttyUSB0 --width 1.8 --address "123 Main St"
"""

import os
import sys
import click

from gps_reader       import record_session, load_csv
from property_boundary import fetch_property_boundary, bounding_box_from_track
from coverage_map      import generate_map


@click.group()
def cli():
    """Spreader GPS Mapper – track lawn treatment coverage."""


# ── record ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--port",  required=True, help="Serial port (e.g. COM3 or /dev/ttyUSB0)")
@click.option("--baud",  default=115200, show_default=True)
@click.option("--out",   default="session.csv", show_default=True, help="Output CSV path")
@click.option("--width", default=1.5, show_default=True,
              help="Spread width in metres (e.g. 1.5 for a 5-ft spreader)")
def record(port, baud, out, width):
    """Record a GPS session from the Arduino to a CSV file."""
    click.echo(f"Spread width set to {width} m  (change with --width)")
    fixes = record_session(port, out, baud)
    click.echo(f"Recorded {len(fixes)} fixes to {out}")


# ── map ───────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--csv",     required=True, help="CSV file from a previous recording")
@click.option("--width",   default=1.5, show_default=True, help="Spread width in metres")
@click.option("--address", default=None,
              help="Street address to look up property boundary (optional)")
@click.option("--out",     default="coverage_map.html", show_default=True)
def map(csv, width, address, out):
    """Generate an HTML coverage map from a saved CSV."""
    fixes = load_csv(csv)
    if not fixes:
        click.echo("No fixes found in CSV.", err=True)
        sys.exit(1)

    click.echo(f"Loaded {len(fixes)} fixes from {csv}")
    boundary = _get_boundary(fixes, address)

    path = generate_map(fixes, width, out, property_polygon=boundary)
    click.echo(f"\nOpen in browser: file://{path}")


# ── run (record + map in one step) ───────────────────────────────────────────

@cli.command()
@click.option("--port",    required=True)
@click.option("--baud",    default=115200, show_default=True)
@click.option("--width",   default=1.5, show_default=True, help="Spread width in metres")
@click.option("--address", default=None)
@click.option("--csv",     default="session.csv", show_default=True)
@click.option("--out",     default="coverage_map.html", show_default=True)
def run(port, baud, width, address, csv, out):
    """Record a session then immediately generate the coverage map."""
    click.echo(f"Spread width: {width} m")
    fixes = record_session(port, csv, baud)
    if not fixes:
        click.echo("No fixes recorded.", err=True)
        sys.exit(1)

    boundary = _get_boundary(fixes, address)
    path = generate_map(fixes, width, out, property_polygon=boundary)
    click.echo(f"\nOpen in browser: file://{path}")


# ── helpers ───────────────────────────────────────────────────────────────────

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
