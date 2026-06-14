# Spreader GPS Mapper

Track where you've treated your yard with a wheeled broadcast spreader.
The Arduino reads your L76X GPS hat and streams coordinates to a PC.
The Python app builds an interactive HTML map showing treated area vs. your property boundary.

---

## Hardware

| Part | Notes |
|------|-------|
| Arduino Uno | Any clone works |
| L76X GPS hat | UART, default 9600 baud |
| USB cable | Data connection to PC |

### Wiring

The L76X hat typically stacks on top of the Uno.  
If yours uses header pins instead:

| L76X pin | Arduino Uno pin |
|----------|-----------------|
| TX       | D4 (SoftwareSerial RX) |
| RX       | D3 (SoftwareSerial TX) |
| VCC      | 3.3 V or 5 V (check your hat) |
| GND      | GND |

---

## Arduino firmware

Open `arduino/gps_logger/gps_logger.ino` in the Arduino IDE and upload to the Uno.  
No extra libraries needed beyond the built-in `SoftwareSerial`.

**Serial commands** (send from Serial Monitor or the Python app):

| Cmd | Action |
|-----|--------|
| `S` | Start logging |
| `P` | Pause logging |
| `C` | Reset record counter |
| `?` | Print status |

**Output line format:**
```
$LOG,2025-06-14T15:30:00Z,40.7128000,-74.0060000,3.20
```

---

## Python app

### Install

```bash
cd app
pip install -r requirements.txt
```

### Property boundary data

The app tries three sources in order:

1. **Regrid** (best accuracy) – free account at <https://regrid.com>  
   Set your key: `export REGRID_API_KEY=your_key_here`

2. **OpenStreetMap Overpass** – no key needed, good for residential neighbourhoods

3. **GPS track bounding box** – automatic fallback if both APIs fail

---

### Usage

**Live session (record + map in one step):**
```bash
python main.py run \
  --port COM5 \            # Windows: COMx  |  Linux/Mac: /dev/ttyUSB0
  --width 1.5 \            # spread width in metres (e.g. 1.5 m ≈ 5 ft)
  --address "123 Main St, Springfield, IL"
```

**Record only (no PC needed later for the map):**
```bash
python main.py record --port /dev/ttyUSB0 --width 1.5 --out lawn.csv
```

**Generate map from a saved CSV:**
```bash
python main.py map --csv lawn.csv --width 1.5 --address "123 Main St, Springfield, IL"
```

Walk the spreader, hit **Ctrl+C** when done. The app saves `coverage_map.html` — open it in any browser.

---

## Map features

- **Blue outline** – property boundary (if found)
- **Green fill** – treated area (GPS track buffered by spread width ÷ 2)
- **Red line** – raw GPS track
- **Info marker** – treated area in m², property area, and % coverage

---

## Spread width reference

| Spreader setting | Approx. width |
|------------------|---------------|
| Narrow (low setting) | 1.0 – 1.5 m |
| Medium | 1.8 – 2.4 m |
| Wide (max setting) | 3.0 – 4.5 m |

Measure your actual spread on a paved surface before your first run for accurate coverage numbers.

---

## Tips

- Wait for the GPS to get a fix (solid green LED on most L76X hats) before starting.
- Walk at a steady pace; the L76X updates at 1 Hz by default.
- Keep overlapping passes to avoid gaps — the map shows you exactly where you've been.
- Save each session CSV with a date in the name (`lawn_2025-06-14.csv`) to compare treatments over time.
