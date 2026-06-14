# Spreader Mapper

A DIY GPS-guided lawn treatment tracking system. Push your broadcast spreader around the yard and see a live map on your iPhone showing where you've already treated — so you can plan the next pass without missing spots or double-treating.

## How it works

A Raspberry Pi Zero 2W reads GPS position from an L76X module and hosts a local web server. Your iPhone connects directly to the Pi's Wi-Fi hotspot (no home network or internet required) and opens a Leaflet map in Safari. As you push the spreader, the system paints green swaths on the map based on your GPS track and selected spread width. A waterproof roller lever switch mounted on the spreader gate automatically turns tracking on and off as the gate opens and closes.

```
[L76X GPS HAT]
      │  UART
[Pi Zero 2WH] ──── Wi-Fi hotspot "SpreaderMap"
      │  GPIO 17                         │
[Gate switch] (auto on/off)        [iPhone Safari]
                                   http://192.168.4.1
```

## Features

- Live coverage map — treated area fills in green as you walk
- Automatic spreading detection — gate switch triggers start/stop, no button presses
- Overlap warning — red banner when you re-enter a treated area
- Spread width slider — set in feet, updates the swath in real time
- Property boundary overlay — fetch from Regrid or OpenStreetMap before going outside
- Coverage stats — acres treated, percent of property covered, session duration
- Session export — download CSV log, track GeoJSON, and coverage polygon GeoJSON
- Parallel guide lines — shows suggested next pass based on spread width
- Fully offline — works with no internet once the boundary is pre-loaded

## Hardware

| Part | Status |
|------|--------|
| Raspberry Pi Zero 2 WH | ✅ Purchased |
| 128GB microSDXC A2/U3 | ✅ Purchased |
| Joinfworld IP67 roller lever switch (SPDT) | ✅ Purchased |
| L76X GPS HAT | 🛒 Still needed |
| USB power bank (20,000 mAh) | 🛒 Still needed |
| 2× momentary buttons (width +/−) | 🛒 Still needed |
| Weatherproof project box | 🛒 Still needed |

## Quick wiring

```
L76X HAT  →  stack directly on 40-pin header (no wires)

Gate switch (SPDT):
  COM  →  Pi Pin 11 (GPIO 17)
  NO   →  Pi Pin 9  (GND)
  NC   →  leave disconnected

Width + button:  GPIO 27 (Pin 13) → GND (Pin 14)
Width − button:  GPIO 22 (Pin 15) → GND (Pin 14)
```

## Quick start

```bash
# 1. Clone and install on the Pi
git clone <repo> ~/spreader-mapper
cd ~/spreader-mapper/spreader-mapper/system
bash install.sh

# 2. Fetch your property boundary (needs internet, do once)
cd ~/spreader-mapper/spreader-mapper/tools
python fetch_boundary.py --address "123 Main St, City, ST" \
  --out ../backend/data/boundaries/boundary.geojson

# 3. Go outside
#    Power on Pi → iPhone connects to "SpreaderMap" (password: spreader1)
#    Open Safari → http://192.168.4.1
#    Tap START SESSION → open spreader gate → walk

# 4. Export session when done
#    Tap END SESSION → Export → download CSV / GeoJSON
```

## Project layout

```
spreader-mapper/
├── backend/          Flask + SocketIO server, GPS reader, session logic
├── frontend/         Leaflet map UI (served by Flask, runs in Safari)
├── tools/            fetch_boundary.py — pre-session property boundary fetch
└── system/           install.sh, setup_hotspot.sh, systemd service
```

Full documentation, wiring diagrams, shopping list, and setup walkthrough:
**[spreader-mapper/README.md](spreader-mapper/README.md)**
