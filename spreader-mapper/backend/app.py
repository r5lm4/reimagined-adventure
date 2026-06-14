"""
app.py - Flask + Flask-SocketIO web server for the GPS spreader mapper.

Serves the frontend, handles REST API calls, emits real-time state via
WebSocket, reads GPIO buttons (optional), and manages session lifecycle.
"""

import json
import os
import time
import logging
from pathlib import Path

from flask import Flask, send_from_directory, jsonify, request, send_file, abort
from flask_socketio import SocketIO

import gps as gps_module
from session import Session
from geometry import (
    load_boundary_geojson,
    generate_guide_lines,
    feet_to_meters,
    shape_to_geojson,
)
from shapely.geometry import mapping

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"
DATA_DIR = BASE_DIR / "data"
BOUNDARIES_DIR = DATA_DIR / "boundaries"
SESSIONS_DIR = DATA_DIR / "sessions"

for d in (BOUNDARIES_DIR, SESSIONS_DIR):
    d.mkdir(parents=True, exist_ok=True)

DEFAULT_BOUNDARY_PATH = BOUNDARIES_DIR / "boundary.geojson"

# ---------------------------------------------------------------------------
# Flask + SocketIO setup
# ---------------------------------------------------------------------------
app = Flask(
    __name__,
    static_folder=str(FRONTEND_DIR),
    static_url_path="/static",
)
app.config["SECRET_KEY"] = "spreader-secret-key"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ---------------------------------------------------------------------------
# GPIO (optional, non-Pi environments skip gracefully)
# ---------------------------------------------------------------------------
GPIO_AVAILABLE = False
try:
    import RPi.GPIO as GPIO

    GPIO.setmode(GPIO.BCM)
    # GPIO 17: spreading toggle, 27: width+, 22: width-
    for pin in (17, 27, 22):
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO_AVAILABLE = True
    logger.info("GPIO: initialized successfully")
except Exception as _gpio_err:
    logger.info(f"GPIO: not available ({_gpio_err}), running without hardware buttons")

# Button state tracking (for edge detection)
_gpio_state = {17: True, 27: True, 22: True}  # True = not pressed (active LOW)

# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------
_state_lock = __import__("threading").Lock()

_spreading = False
_spread_width_ft = 12.0
_pass_spacing_ft = 10.0
_session: Session | None = None
_paused = False
_boundary_poly = None
_boundary_geojson: dict | None = None
_last_swath_geojson = None
_last_overlap_fraction = 0.0


def _load_boundary(path: str) -> bool:
    """Load a boundary GeoJSON file into global state. Returns True on success."""
    global _boundary_poly, _boundary_geojson
    poly = load_boundary_geojson(path)
    if poly is None:
        return False
    try:
        with open(path) as f:
            _boundary_geojson = json.load(f)
    except Exception:
        _boundary_geojson = {"type": "Feature", "geometry": mapping(poly), "properties": {}}
    _boundary_poly = poly
    logger.info(f"Boundary loaded from {path}")
    return True


# Auto-load boundary on startup
if DEFAULT_BOUNDARY_PATH.exists():
    _load_boundary(str(DEFAULT_BOUNDARY_PATH))

# ---------------------------------------------------------------------------
# Background thread
# ---------------------------------------------------------------------------

def _background_loop():
    """Runs every second: reads GPS, updates session, emits state."""
    global _spreading, _spread_width_ft, _last_swath_geojson, _last_overlap_fraction
    global _gpio_state

    logger.info("Background loop started")
    while True:
        socketio.sleep(1)

        fix = gps_module.get_fix()

        # GPIO polling (edge detection)
        if GPIO_AVAILABLE:
            try:
                import RPi.GPIO as GPIO

                # Spreading toggle (GPIO 17) - falling edge
                current_17 = GPIO.input(17)
                if not current_17 and _gpio_state[17]:  # pressed
                    with _state_lock:
                        _spreading = not _spreading
                    logger.info(f"GPIO: spreading toggled to {_spreading}")
                _gpio_state[17] = current_17

                # Width+ (GPIO 27)
                current_27 = GPIO.input(27)
                if not current_27 and _gpio_state[27]:
                    with _state_lock:
                        _spread_width_ft = min(24.0, _spread_width_ft + 1.0)
                    logger.info(f"GPIO: width+ -> {_spread_width_ft} ft")
                _gpio_state[27] = current_27

                # Width- (GPIO 22)
                current_22 = GPIO.input(22)
                if not current_22 and _gpio_state[22]:
                    with _state_lock:
                        _spread_width_ft = max(4.0, _spread_width_ft - 1.0)
                    logger.info(f"GPIO: width- -> {_spread_width_ft} ft")
                _gpio_state[22] = current_22

            except Exception as e:
                logger.warning(f"GPIO poll error: {e}")

        # Session update
        with _state_lock:
            sess = _session
            spreading = _spreading
            paused = _paused
            width_ft = _spread_width_ft

        if sess is not None and not paused and fix.get("valid", False):
            result = sess.add_fix(fix, spreading)
            with _state_lock:
                _last_swath_geojson = result.get("new_swath_geojson")
                _last_overlap_fraction = result.get("overlap_fraction", 0.0)

        # Build and emit state
        state = _build_state(fix)
        socketio.emit("state", state)


def _build_state(fix: dict) -> dict:
    """Assemble the full state payload for SocketIO emission."""
    with _state_lock:
        sess = _session
        spreading = _spreading
        paused = _paused
        width_ft = _spread_width_ft
        spacing_ft = _pass_spacing_ft
        swath = _last_swath_geojson
        overlap = _last_overlap_fraction
        has_boundary = _boundary_poly is not None

    stats = {}
    coverage_geojson = None
    track_geojson = None

    if sess is not None:
        stats = sess.get_stats()
        coverage_geojson = sess.get_coverage_geojson()
        track_geojson = sess.get_track_geojson()

    return {
        "position": {
            "lat": fix.get("lat"),
            "lon": fix.get("lon"),
            "valid": fix.get("valid", False),
            "fix_quality": fix.get("fix_quality", 0),
            "satellites": fix.get("satellites", 0),
            "hdop": fix.get("hdop", 99.9),
            "speed_kmh": fix.get("speed_kmh", 0.0),
            "timestamp": fix.get("timestamp"),
        },
        "spreading": spreading,
        "paused": paused,
        "session_active": sess is not None,
        "spread_width_ft": width_ft,
        "pass_spacing_ft": spacing_ft,
        "coverage_geojson": coverage_geojson,
        "track_geojson": track_geojson,
        "current_swath_geojson": swath,
        "overlap_fraction": overlap,
        "stats": stats,
        "boundary_loaded": has_boundary,
    }


# ---------------------------------------------------------------------------
# SocketIO events
# ---------------------------------------------------------------------------

@socketio.on("connect")
def on_connect():
    logger.info(f"Client connected: {request.sid}")
    # Send immediate state on connect
    fix = gps_module.get_fix()
    state = _build_state(fix)
    socketio.emit("state", state, to=request.sid)


@socketio.on("disconnect")
def on_disconnect():
    logger.info(f"Client disconnected: {request.sid}")


# ---------------------------------------------------------------------------
# Routes - Frontend
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(str(FRONTEND_DIR), "index.html")


@app.route("/static/js/<path:filename>")
def static_js(filename):
    return send_from_directory(str(FRONTEND_DIR / "js"), filename)


@app.route("/static/css/<path:filename>")
def static_css(filename):
    return send_from_directory(str(FRONTEND_DIR / "css"), filename)


# ---------------------------------------------------------------------------
# Routes - API
# ---------------------------------------------------------------------------

@app.route("/api/state")
def api_state():
    fix = gps_module.get_fix()
    return jsonify(_build_state(fix))


@app.route("/api/boundary")
def api_boundary():
    with _state_lock:
        bgeojson = _boundary_geojson
    if bgeojson is None:
        abort(404, description="No boundary loaded")
    return jsonify(bgeojson)


@app.route("/api/session/start", methods=["POST"])
def api_session_start():
    global _session, _spread_width_ft, _pass_spacing_ft, _last_swath_geojson, _last_overlap_fraction

    data = request.get_json(force=True, silent=True) or {}
    width_ft = float(data.get("spread_width_ft", _spread_width_ft))
    spacing_ft = float(data.get("pass_spacing_ft", _pass_spacing_ft))

    with _state_lock:
        if _session is not None:
            return jsonify({"error": "Session already active"}), 400
        _spread_width_ft = width_ft
        _pass_spacing_ft = spacing_ft
        _last_swath_geojson = None
        _last_overlap_fraction = 0.0
        _session = Session(
            spread_width_ft=width_ft,
            pass_spacing_ft=spacing_ft,
            boundary_poly=_boundary_poly,
            log_dir=str(SESSIONS_DIR),
        )

    logger.info(f"Session started: width={width_ft}ft spacing={spacing_ft}ft")
    return jsonify({"status": "started", "spread_width_ft": width_ft, "pass_spacing_ft": spacing_ft})


@app.route("/api/session/stop", methods=["POST"])
def api_session_stop():
    global _session

    with _state_lock:
        sess = _session
        _session = None

    if sess is None:
        return jsonify({"error": "No active session"}), 400

    csv_path = sess.export_csv()
    track_path = sess.export_geojson_track()
    coverage_path = sess.export_geojson_coverage()
    stats = sess.get_stats()
    sess.close()

    logger.info(f"Session stopped. CSV: {csv_path}")
    return jsonify({
        "status": "stopped",
        "stats": stats,
        "files": {
            "csv": csv_path,
            "track_geojson": track_path,
            "coverage_geojson": coverage_path,
        },
    })


@app.route("/api/session/pause", methods=["POST"])
def api_session_pause():
    global _paused
    with _state_lock:
        _paused = not _paused
        paused = _paused
    return jsonify({"paused": paused})


@app.route("/api/settings", methods=["POST"])
def api_settings():
    global _spreading, _spread_width_ft, _pass_spacing_ft
    data = request.get_json(force=True, silent=True) or {}

    with _state_lock:
        if "spreading" in data:
            _spreading = bool(data["spreading"])
        if "spread_width_ft" in data:
            val = float(data["spread_width_ft"])
            _spread_width_ft = max(4.0, min(24.0, val))
        if "pass_spacing_ft" in data:
            val = float(data["pass_spacing_ft"])
            _pass_spacing_ft = max(4.0, min(30.0, val))

        return jsonify({
            "spreading": _spreading,
            "spread_width_ft": _spread_width_ft,
            "pass_spacing_ft": _pass_spacing_ft,
        })


@app.route("/api/guide_lines")
def api_guide_lines():
    with _state_lock:
        bgeojson = _boundary_geojson
        spacing_ft = _pass_spacing_ft

    spacing_m = feet_to_meters(spacing_ft)
    guide = generate_guide_lines(bgeojson, spacing_m)
    return jsonify(guide)


@app.route("/api/boundary/load", methods=["POST"])
def api_boundary_load():
    global _boundary_poly, _boundary_geojson
    data = request.get_json(force=True, silent=True) or {}
    path = data.get("path", "")
    if not path:
        return jsonify({"error": "path required"}), 400
    if not os.path.exists(path):
        return jsonify({"error": f"File not found: {path}"}), 404
    ok = _load_boundary(path)
    if not ok:
        return jsonify({"error": "Could not parse boundary GeoJSON"}), 400
    return jsonify({"status": "loaded", "path": path})


@app.route("/api/boundary/files")
def api_boundary_files():
    try:
        files = [
            str(p.name)
            for p in BOUNDARIES_DIR.iterdir()
            if p.suffix == ".geojson"
        ]
    except Exception:
        files = []
    return jsonify({"files": files, "directory": str(BOUNDARIES_DIR)})


# ---------------------------------------------------------------------------
# Export download routes
# ---------------------------------------------------------------------------

@app.route("/api/export/csv")
def api_export_csv():
    with _state_lock:
        sess = _session
    if sess is None:
        # Try to find the most recent session CSV
        csvs = sorted(SESSIONS_DIR.glob("session_*.csv"), reverse=True)
        if not csvs:
            abort(404, description="No session CSV available")
        return send_file(str(csvs[0]), as_attachment=True, download_name=csvs[0].name)
    path = sess.export_csv()
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))


@app.route("/api/export/geojson/track")
def api_export_track():
    with _state_lock:
        sess = _session
    if sess is None:
        tracks = sorted(SESSIONS_DIR.glob("track_*.geojson"), reverse=True)
        if not tracks:
            abort(404, description="No track file available")
        return send_file(str(tracks[0]), as_attachment=True, download_name=tracks[0].name)
    path = sess.export_geojson_track()
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))


@app.route("/api/export/geojson/coverage")
def api_export_coverage():
    with _state_lock:
        sess = _session
    if sess is None:
        coverages = sorted(SESSIONS_DIR.glob("coverage_*.geojson"), reverse=True)
        if not coverages:
            abort(404, description="No coverage file available")
        return send_file(str(coverages[0]), as_attachment=True, download_name=coverages[0].name)
    path = sess.export_geojson_coverage()
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def _start_background():
    """Start GPS reader and background loop after first SocketIO connection."""
    gps_module.start(port="/dev/serial0", baud=9600)
    socketio.start_background_task(_background_loop)


if __name__ == "__main__":
    logger.info("Starting spreader mapper backend...")
    gps_module.start(port="/dev/serial0", baud=9600)
    socketio.start_background_task(_background_loop)
    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        debug=False,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )
