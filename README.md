# Spreader GPS Mapper

Track where you've treated your yard with a wheeled broadcast spreader.
The Arduino logs GPS tracks directly to an SD card — no laptop needed outside.
Bring the card in, run the Python app, get an interactive HTML coverage map.

---

## Hardware you need

| Part | Notes |
|------|-------|
| Arduino Uno | Any clone |
| L76X GPS hat | UART, 9600 baud |
| SD card hat/shield | SPI, CS on pin 10 |
| Momentary push button | Start/stop sessions |
| LED + 220 Ω resistor | Status indicator |
| microSD card | FAT32 formatted |

---

## Wiring

### L76X GPS hat → Arduino

| L76X pin | Arduino pin |
|----------|-------------|
| TX | D7 (SoftwareSerial RX) |
| RX | D8 (SoftwareSerial TX, optional) |
| VCC | 3.3 V or 5 V (check your hat's spec) |
| GND | GND |

### SD card hat → Arduino

Most SD hats use the standard SPI pins — just plug it in.  
If yours has a jumper-selectable CS pin, set it to **D10**.

| SD hat pin | Arduino pin |
|------------|-------------|
| CS | D10 |
| MOSI | D11 (hardware SPI) |
| MISO | D12 (hardware SPI) |
| SCK | D13 (hardware SPI) |

### Button and LED

| Component | Arduino pin |
|-----------|-------------|
| Button (one leg) | D2 |
| Button (other leg) | GND |
| LED anode (+) | D6 via 220 Ω |
| LED cathode (−) | GND |

---

## Arduino firmware

Open `arduino/gps_logger/gps_logger.ino` in the Arduino IDE and upload.  
No extra libraries needed — `SD` and `SoftwareSerial` are both built-in.

If your SD hat uses a CS pin other than 10, change `SD_CS_PIN` near the top of the sketch.

### LED status codes

| Pattern | Meaning |
|---------|---------|
| Slow blink (1 s) | Waiting for GPS fix |
| Fast blink (0.2 s) | Actively logging to SD |
| Off | Paused / idle (fix present) |
| Solid ON | SD card error |

### Using it in the yard

1. Power on, wait for the LED to switch from slow blink → fast blink (GPS fix acquired).  
   This typically takes 30–90 seconds outdoors with a clear sky.
2. **Press the button** to start a new session. LED goes fast-blink.
3. Push the spreader across your yard in parallel passes.
4. **Press the button again** to stop. The file is safely closed.
5. You can do multiple start/stop sessions; each creates a new file: `LOG001.CSV`, `LOG002.CSV` …
6. Power off and bring the SD card inside.

---

## Python app

### Install

```bash
cd app
pip install -r requirements.txt
```

### Property boundary (optional but recommended)

The app tries two sources in order:

1. **Regrid** (best accuracy) — free account at <https://regrid.com>  
   After signing up: `export REGRID_API_KEY=your_key_here`
2. **OpenStreetMap Overpass** — no key needed
3. **GPS track bounding box** — automatic fallback if both APIs fail

### Usage

**Map a single session:**
```bash
python main.py map \
  --csv E:/LOG001.CSV \
  --width 1.5 \
  --address "123 Main St, Springfield, IL"
```

**Map every session on the card (one HTML file per session):**
```bash
python main.py mapall \
  --card E:/ \
  --width 1.5 \
  --address "123 Main St, Springfield, IL"
```

**Merge all sessions into one cumulative map** (shows total coverage across multiple days):
```bash
python main.py merge \
  --card E:/ \
  --width 1.5 \
  --address "123 Main St, Springfield, IL"
```

On Linux/Mac, replace `E:/` with the SD card mount path (e.g. `/media/yourname/SD`).

---

## Map features

| Layer | Description |
|-------|-------------|
| Blue outline | Property boundary (from Regrid or OSM) |
| Green fill | Treated area (GPS track buffered by spread width ÷ 2) |
| Red line | Raw GPS track |
| Info marker | Treated m², property m², and % coverage |

---

## Spread width reference

Measure your actual spread on pavement before your first run for accurate numbers.

| Typical setting | Approx. width |
|-----------------|---------------|
| Narrow | 1.0 – 1.5 m (3 – 5 ft) |
| Medium | 1.8 – 2.4 m (6 – 8 ft) |
| Wide | 3.0 – 4.5 m (10 – 15 ft) |

---

## Tips

- Wait for the fast-blink before starting your first pass — no fix means no GPS data.
- Keep passes overlapping slightly; the map will show any gaps.
- Name your output files by date (`LOG001_2025-06-14_map.html`) to track coverage over time.
- The `merge` command is great for multi-day fertilizer programs — run it each time to see cumulative coverage.
