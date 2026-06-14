# Spreader Mapper

GPS-based lawn treatment mapping system for Raspberry Pi Zero 2W + iPhone.
Tracks coverage in real time, detects overlap, and exports session data.

---

## Shopping List

| Part | Why Needed | Approx Price |
|------|-----------|-------------|
| Raspberry Pi Zero 2W | Main computer. Quad-core 1GHz, runs Flask + Shapely comfortably | ~$15 |
| 32GB microSD card (Class 10 / A1) | OS + data storage. Class 10 required for reliable write speed | ~$8 |
| Pi Zero case with GPIO clearance | Protects the Pi; choose one that allows header access if using buttons | ~$8 |
| USB-A to micro-USB data cable | Initial setup / SSH connection from PC | ~$5 |
| MicroUSB power adapter (5V 2.5A) | Bench power during initial setup | ~$10 |
| 20,000 mAh USB power bank (5V stable) | Field power. Must provide stable 5V/1A+ — cheap banks can brown out the Pi under load | ~$25 |
| Waveshare L76X GPS HAT | Stacks directly on 40-pin header, uses hardware UART. Pre-tuned for fast fix | ~$20 |
| 3× momentary push buttons (normally open) | Width +, Width −, Spreading toggle (optional hardware controls) | ~$5/pack |
| Female-to-female jumper wires | Connect buttons to GPIO header pins | ~$6 |
| Small breadboard | Prototype button connections before soldering | ~$4 |
| Small weatherproof project box | Encloses Pi + power bank for outdoor use | ~$12 |
| Velcro straps or zip ties | Mount the box to spreader frame | ~$5 |
| *Optional:* Rotary encoder | More intuitive width adjustment than +/− buttons | ~$8 |
| *Optional:* 3-position toggle switch | Hardware spreading ON / OFF / PAUSE | ~$8 |

**Total: approximately $100–130 for a complete build.**

---

## Wiring Diagram

```
Raspberry Pi Zero 2W — 40-pin GPIO Header
(Left column = odd pins, right column = even pins)

Pin 1  [3.3V  ] ─── [5V    ] Pin 2
Pin 3  [GPIO2 ] ─── [5V    ] Pin 4
Pin 5  [GPIO3 ] ─── [GND   ] Pin 6
Pin 7  [GPIO4 ] ─── [GPIO14] Pin 8  ← GPS TX (L76X HAT: connects automatically)
Pin 9  [GND   ] ─── [GPIO15] Pin 10 ← GPS RX (L76X HAT: connects automatically)
Pin 11 [GPIO17] ─── [GPIO18] Pin 12   ← Spreading toggle button → GND (pin 9)
Pin 13 [GPIO27] ─── [GND   ] Pin 14   ← Width+ button → GND (pin 14)
Pin 15 [GPIO22] ─── [GPIO23] Pin 16   ← Width− button → GND (pin 9 or 14)
...
```

**L76X GPS HAT:** Simply stack onto the 40-pin header. The HAT uses UART on
GPIO14 (TX) and GPIO15 (RX), which become `/dev/serial0` after enabling UART.
No separate wiring needed for GPS.

**Buttons:** Each button connects between the listed GPIO pin and any GND pin.
The software uses internal pull-up resistors, so the button press pulls the
pin LOW (active LOW logic). No external resistors required.

```
GPIO17 (Pin 11) ──┤ BTN ├── GND (Pin 9)   → Spreading ON/OFF toggle
GPIO27 (Pin 13) ──┤ BTN ├── GND (Pin 14)  → Width + (increases spread width 1 ft)
GPIO22 (Pin 15) ──┤ BTN ├── GND (Pin 14)  → Width − (decreases spread width 1 ft)
```

---

## Your 10 Questions Answered

**1. Is the Pi Zero 2W powerful enough?**
Yes. The Pi Zero 2W has a quad-core ARM Cortex-A53 at 1GHz with 512MB RAM.
Flask + SocketIO + Shapely polygon math runs well under 30% CPU in normal use.
The GPS loop runs at 1Hz (one fix per second), which is leisurely for the Pi.
A typical session with 1,000 GPS fixes and a moderate coverage polygon will use
~50MB RAM — well within budget.

**2. Exact hardware list** — See Shopping List table above.

**3. L76X HAT connection**
The Waveshare L76X GPS HAT stacks directly on the Pi's 40-pin GPIO header.
It uses the Pi's hardware UART (GPIO14 = TXD, GPIO15 = RXD), which appears as
`/dev/serial0` after you enable UART in `/boot/firmware/config.txt`. The install
script handles this automatically.

**4. gpsd vs pyserial**
This system uses **pyserial directly** — no gpsd daemon. Reasons:
- gpsd adds significant complexity (another daemon, client library, socket protocol)
- pyserial is simpler: open serial port, read lines, parse NMEA
- The L76X outputs standard NMEA at 9600 baud; pyserial handles it with 30 lines of code
- gpsd is worthwhile when multiple programs need GPS simultaneously; here only our app does

**5. Python architecture**
Flask HTTP server + Flask-SocketIO WebSocket server, single process.
Background thread (via `socketio.start_background_task`) reads GPS at 1Hz,
updates the session, and broadcasts state to connected clients. Thread safety
via a single threading.Lock. The Session class incrementally unions Shapely
polygons as the spreader moves.

**6. Leaflet frontend**
Leaflet.js 1.9.4 runs entirely offline (downloaded to the Pi by `install.sh`).
Socket.IO client receives state updates every second and redraws map layers.
The iPhone connects to the Pi's WiFi hotspot and opens Safari to
`http://192.168.4.1:5000`.

**7. Coordinate projection**
All buffering and area calculations use UTM projection via pyproj.
The correct UTM zone is auto-selected from longitude (zone = floor((lon+180)/6)+1).
Example: longitude -93.5 → UTM Zone 15N (EPSG:32615). Buffer width is applied
in meters in UTM space, then the polygon is projected back to WGS84 for storage
and display. This gives accurate swath widths regardless of latitude.

**8. WiFi access point**
`system/setup_hotspot.sh` configures `hostapd` (the AP daemon) + `dnsmasq`
(DHCP server) + a static IP on wlan0. SSID: `SpreaderMap`, password:
`spreader1`. After reboot the Pi broadcasts its own WiFi network. Note: wlan0
will no longer connect to your home router — use eth0 or USB ethernet for
internet access during setup.

**9. Property boundary storage**
Boundaries are GeoJSON files stored in `backend/data/boundaries/`.
The default file `boundary.geojson` is auto-loaded on startup.
Use `tools/fetch_boundary.py` to fetch from OpenStreetMap Overpass API or
Regrid (commercial, more accurate parcel data). You can also draw a boundary
at https://geojson.io and copy the file to the Pi.

**10. Build plan**
See V1/V2/V3 Roadmap section below.

---

## Step-by-Step Setup

### Step 1: Flash the SD card
1. Download Raspberry Pi Imager from https://www.raspberrypi.com/software/
2. Choose **Raspberry Pi OS Lite (64-bit)** — no desktop needed
3. Click the gear icon and configure:
   - Hostname: `spreader`
   - Enable SSH (use password authentication)
   - Username: `pi`, Password: your choice
   - Do NOT configure WiFi (the hotspot script will take over wlan0)
4. Flash to microSD card

### Step 2: First boot and SSH
1. Insert SD card, connect Pi Zero via micro-USB data cable to your computer
2. Wait ~60 seconds for boot
3. SSH in: `ssh pi@spreader.local` (or find IP via your router)

### Step 3: Copy files to the Pi
```bash
# From your computer, in the spreader-mapper directory:
scp -r . pi@spreader.local:/home/pi/spreader-mapper/
```

### Step 4: Run the install script
```bash
ssh pi@spreader.local
cd /home/pi/spreader-mapper
sudo bash system/install.sh
```

This will install packages, download Leaflet + Socket.IO, enable UART,
configure the hotspot, and install the systemd service. Takes 5–15 minutes.

### Step 5: Fetch your property boundary (while Pi has internet)
```bash
# While still SSH'd in (before hotspot takes over wlan0):
cd /home/pi/spreader-mapper/tools
python3 fetch_boundary.py \
    --address "123 Main St, Springfield, IL" \
    --out ../backend/data/boundaries/boundary.geojson
```

Or on your PC (install `requests shapely pyproj` first):
```bash
cd spreader-mapper/tools
pip install requests shapely pyproj
python3 fetch_boundary.py \
    --address "123 Main St, Springfield, IL" \
    --out boundary.geojson

# Then copy to Pi:
scp boundary.geojson pi@spreader.local:/home/pi/spreader-mapper/backend/data/boundaries/boundary.geojson
```

### Step 6: Reboot and connect
```bash
sudo reboot
```

After reboot:
1. On your iPhone, go to **Settings → WiFi**
2. Connect to **SpreaderMap** (password: `spreader1`)
3. Open **Safari** → `http://192.168.4.1:5000`
4. The map will load with your property boundary

### Step 7: Attach the GPS HAT
With the Pi powered off, stack the L76X HAT onto the 40-pin header.
Power back on — the GPS needs 30–60 seconds outdoors to acquire satellites.

---

## Using the System

### Typical workflow

1. **Before going out:** Fetch boundary on PC, copy to Pi
2. **Power on** the Pi + power bank (LED will blink as it boots, ~45 seconds)
3. **Connect iPhone** to SpreaderMap WiFi
4. **Open Safari** → `http://192.168.4.1:5000`
5. **Wait for GPS fix** — green dot appears, satellite count shows
6. **Fit map** to property with the ⊊ Fit button
7. **Tap START SESSION** when ready to begin
8. **Toggle SPREADING ON** when you start spreading (or use the GPIO button)
9. Walk/drive your passes — treated area fills in green
10. **Watch for OVERLAP** warning (red banner) if you're double-covering
11. **Toggle SPREADING OFF** for turns, refill stops, or gaps
12. **END SESSION** when done — exports are saved automatically
13. **Export** via the 💾 button — download CSV and GeoJSON files

### Controls

| Control | Function |
|---------|----------|
| START SESSION | Begin recording. Creates CSV and GeoJSON export files |
| END SESSION | Stop recording. Finalizes exports |
| PAUSE / RESUME | Pause processing without ending session |
| SPREADING ON/OFF | Toggle whether the current movement is treated |
| Width −/+ | Adjust spreader width in 1 ft increments (4–24 ft) |
| Spacing −/+ | Adjust guide line spacing (4–30 ft) |
| ⊊ Fit | Zoom map to fit property boundary |
| ⊙ Follow | Toggle auto-pan to follow GPS position |
| 💾 Export | Download CSV log, track GeoJSON, coverage GeoJSON |

### Overlap detection
The system flags overlap when >15% of a new swath polygon is already covered.
The red "⚠ OVERLAP DETECTED" banner appears and fades when you move to
uncovered ground. Overlap data is saved in the CSV for analysis.

---

## V1/V2/V3 Roadmap

### V1 — Core GPS Tracking (Current)
- [x] Real-time GPS position display on Leaflet map
- [x] Track recording and CSV export
- [x] Coverage area calculation and display
- [x] Overlap detection
- [x] Spreading on/off toggle
- [x] Spread width adjustment
- [x] WiFi hotspot for iPhone connection
- [x] Session export (CSV + GeoJSON)

### V2 — Property Boundary
- [x] Property boundary display (blue dashed line)
- [x] Coverage percentage calculation relative to boundary
- [x] Coverage clipping to boundary
- [x] Guide lines showing suggested passes
- [ ] Multiple boundary files / selector UI
- [ ] Offline map tiles (download neighborhood tiles for no-internet use)
- [ ] Manual boundary drawing in the UI

### V3 — Advanced Guidance
- [ ] Voice cues ("turn left", "25 feet to boundary")
- [ ] Rate control integration (PWM signal to spreader rate valve)
- [ ] Wind direction overlay (affects actual spread pattern)
- [ ] Season tracking (multiple sessions on same property over time)
- [ ] Coverage history: compare this year vs last year
- [ ] Export to QGIS / Google Earth format
- [ ] Rotary encoder for smoother width adjustment
- [ ] OLED display on Pi showing fix quality and speed (no phone needed)

---

## Troubleshooting

**No GPS fix (red dot, 0 sats)**
- GPS needs clear sky view — move outside away from buildings/trees
- Check that UART is enabled: `cat /boot/firmware/config.txt | grep uart`
- Check serial port: `sudo cat /dev/serial0` should show NMEA sentences
- Verify pi is in dialout group: `groups pi`

**Can't connect to SpreaderMap WiFi**
- Wait 60 seconds after power-on for hotspot to start
- Check hotspot status: `sudo systemctl status hostapd`
- Check Pi IP: `ip addr show wlan0` (should show 192.168.4.1)

**App won't load (can't reach 192.168.4.1:5000)**
- Check service: `sudo systemctl status spreader`
- Check logs: `sudo journalctl -u spreader -f`
- Restart service: `sudo systemctl restart spreader`

**Coverage polygon looks wrong**
- Ensure GPS has a good fix (HDOP < 3.0, 6+ satellites) before starting session
- A poor fix position jumps can create incorrect swath polygons

**Session files / exports**
Session files are saved to `backend/data/sessions/`:
- `session_YYYYMMDD_HHMMSS.csv` — full log
- `track_YYYYMMDD_HHMMSS.geojson` — GPS track
- `coverage_YYYYMMDD_HHMMSS.geojson` — covered area polygon

---

## File Structure

```
spreader-mapper/
├── backend/
│   ├── app.py          ← Flask + SocketIO server (main process)
│   ├── gps.py          ← NMEA parser, background serial reader
│   ├── session.py      ← Session tracking, swath union, CSV log
│   ├── geometry.py     ← pyproj/Shapely GIS operations
│   ├── requirements.txt
│   └── data/
│       ├── boundaries/ ← Property GeoJSON files
│       └── sessions/   ← Export CSV and GeoJSON files
├── frontend/
│   ├── index.html      ← Single-page app for iPhone Safari
│   ├── js/
│   │   ├── map.js      ← Leaflet + Socket.IO frontend logic
│   │   ├── leaflet.js  ← Downloaded by install.sh
│   │   └── socket.io.min.js
│   └── css/
│       ├── style.css
│       ├── leaflet.css
│       └── images/     ← Leaflet marker images
├── tools/
│   └── fetch_boundary.py  ← CLI: fetch property boundary from OSM/Regrid
└── system/
    ├── install.sh        ← One-shot install for fresh Pi OS Lite
    ├── setup_hotspot.sh  ← Configure WiFi access point
    └── spreader.service  ← systemd unit file
```
