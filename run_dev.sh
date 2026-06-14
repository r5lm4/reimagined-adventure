#!/bin/bash
# run_dev.sh — Run the spreader mapper on your desktop for testing.
# No Raspberry Pi, GPS hardware, or GPIO needed.
#
# Usage:
#   bash run_dev.sh
#
# The simulated GPS walks a small rectangle so you can watch the coverage
# map fill in. Open http://localhost:5000 in your browser when it starts.
#
# Optional env vars:
#   SIM_LAT=30.4515   Starting latitude  (default: Baton Rouge, LA)
#   SIM_LON=-91.1871  Starting longitude

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$SCRIPT_DIR/spreader-mapper/backend"
FRONTEND="$SCRIPT_DIR/spreader-mapper/frontend"

echo "=== Spreader Mapper — dev mode ==="
echo ""

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install Python 3.10+."
    exit 1
fi

PYTHON=python3

# Create virtualenv if it doesn't exist
VENV="$SCRIPT_DIR/.venv"
if [ ! -d "$VENV" ]; then
    echo "Creating virtual environment..."
    $PYTHON -m venv "$VENV"
fi

source "$VENV/bin/activate"

# Install / upgrade dependencies
echo "Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r "$BACKEND/requirements.txt"

# Download Leaflet and Socket.IO locally if not already there
JS_DIR="$FRONTEND/js"
CSS_DIR="$FRONTEND/css"
IMG_DIR="$CSS_DIR/images"
mkdir -p "$JS_DIR" "$CSS_DIR" "$IMG_DIR"

if [ ! -f "$JS_DIR/leaflet.js" ]; then
    echo "Downloading Leaflet 1.9.4..."
    curl -sL "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"     -o "$JS_DIR/leaflet.js"
    curl -sL "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"    -o "$CSS_DIR/leaflet.css"
    curl -sL "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png"   -o "$IMG_DIR/marker-icon.png"
    curl -sL "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png" -o "$IMG_DIR/marker-icon-2x.png"
    curl -sL "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png" -o "$IMG_DIR/marker-shadow.png"
fi

if [ ! -f "$JS_DIR/socket.io.min.js" ]; then
    echo "Downloading Socket.IO client 4.7.5..."
    curl -sL "https://cdn.socket.io/4.7.5/socket.io.min.js" -o "$JS_DIR/socket.io.min.js"
fi

# Create data directories
mkdir -p "$BACKEND/data/boundaries" "$BACKEND/data/sessions"

echo ""
echo "Starting server in simulation mode..."
echo "Open your browser to: http://localhost:5000"
echo ""
echo "The simulated GPS walks a rectangle — tap START SESSION then"
echo "SPREADING ON in the app to watch the coverage map fill in."
echo ""
echo "Press Ctrl+C to stop."
echo ""

cd "$BACKEND"
export SIM_GPS=1
export SIM_LAT="${SIM_LAT:-30.4515}"
export SIM_LON="${SIM_LON:-91.1871}"
export FLASK_ENV=development

$PYTHON app.py
