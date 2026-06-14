"""
main.py – CLI for the Spreader GPS Mapper

The Arduino logs GPS tracks directly to the SD card as LOG001.CSV, LOG002.CSV …
Pull the card, plug it into your PC, then run one of these commands:

  # Map a single session file
  python main.py map --csv E:/LOG001.CSV --width 1.5 --address "123 Main St, Anytown, USA"

  # Map every session on the card at once (one map per file)
  python main.py mapall --card E:/ --width 1.5 --address "123 Main St, Anytown, USA"

  # Merge all sessions on the card into a single cumulative map
  python main.py merge --card E:/ --width 1.5 --address "123 Main St, Anytown, USA"
"""

import os
import sys
import click

from gps_reader        import load_csv, load_all_csvs
from property_boundary import fetch_property_boundary, bounding_box_from_track
from coverage_map      import generate_map


@click.group()
def cli():
    """Spreader GPS Mapper – turn SD card CSV logs into coverage maps."""


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
