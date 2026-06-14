"""
mapper.py  –  Main entry point for the Pi live spreader map.

Run on the Raspberry Pi:
    python mapper.py --boundary BOUNDARY.CSV --gps-port /dev/serial0

Controls (touchscreen)
──────────────────────
  [START / STOP] button   Begin or end a logging session
  Width slider            Drag left/right to set spread width (0.5 – 6.0 m)
  [CLEAR] button          Wipe the current session's coverage (asks to confirm)

The display is designed for an 800×480 screen in landscape mode.
It will also work at other resolutions – layout scales automatically.
"""

import sys
import os
import csv
import time
import datetime
import math
import argparse

import pygame

import gps_reader
from map_view import MapView


# ── Layout constants (designed for 800×480) ───────────────────────────────────
HUD_HEIGHT   = 80      # bottom strip height
MAP_MARGIN   = 4       # px gap around map area
MIN_WIDTH_M  = 0.5
MAX_WIDTH_M  = 6.0
DEFAULT_WIDTH_M = 1.5

# Colours
C_HUD_BG     = (20,  20,  20)
C_BTN_START  = (30, 160,  60)
C_BTN_STOP   = (180,  40,  40)
C_BTN_CLEAR  = (60,  60, 160)
C_BTN_TEXT   = (255, 255, 255)
C_SLIDER_BG  = (50,  50,  50)
C_SLIDER_FG  = (80, 200, 120)
C_SLIDER_KNB = (220, 220, 220)
C_TEXT       = (220, 220, 220)
C_FIX_YES    = (80, 220,  80)
C_FIX_NO     = (220,  80,  80)
C_WARN_BG    = (180,  60,  10)


class SpreaderMapper:

    def __init__(self, boundary_csv: str | None, gps_port: str,
                 log_dir: str, fullscreen: bool):
        pygame.init()
        pygame.mouse.set_visible(True)

        info = pygame.display.Info()
        self.W = info.current_w if fullscreen else 800
        self.H = info.current_h if fullscreen else 480
        flags = pygame.FULLSCREEN if fullscreen else 0
        self.screen = pygame.display.set_mode((self.W, self.H), flags)
        pygame.display.set_caption("Spreader Mapper")

        self.font_lg = pygame.font.SysFont("dejavusansmono", 22, bold=True)
        self.font_sm = pygame.font.SysFont("dejavusansmono", 16)
        self.font_xs = pygame.font.SysFont("dejavusansmono", 13)

        self.hud_rect = pygame.Rect(0, self.H - HUD_HEIGHT, self.W, HUD_HEIGHT)
        map_rect = pygame.Rect(
            MAP_MARGIN, MAP_MARGIN,
            self.W - MAP_MARGIN * 2,
            self.H - HUD_HEIGHT - MAP_MARGIN * 2,
        )

        # Load boundary.
        boundary_pts = []
        if boundary_csv and os.path.exists(boundary_csv):
            boundary_pts = _load_boundary(boundary_csv)
            print(f"Loaded {len(boundary_pts)} boundary points")
        else:
            print("No boundary file – map will centre on first GPS fix")

        self.map_view = MapView(map_rect, boundary_pts)
        self.map_rect = map_rect

        # State.
        self.spread_width_m = DEFAULT_WIDTH_M
        self.logging         = False
        self.session_num     = 0
        self.record_count    = 0
        self.log_file        = None
        self.log_dir         = log_dir
        self.last_fix_lat    = None
        self.last_fix_lon    = None
        self.last_log_time   = 0
        self.confirm_clear   = False
        self.confirm_timer   = 0

        # HUD layout (computed once, updated on resize).
        self._layout_hud()

        # Slider drag state.
        self._slider_dragging = False

        # Start GPS thread.
        gps_reader.start(gps_port)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        clock = pygame.time.Clock()
        while True:
            dt_ms = clock.tick(10)   # 10 fps is enough for a walking-pace map
            for event in pygame.event.get():
                self._handle_event(event)
            self._update()
            self._draw()
            pygame.display.flip()

    # ── Update ────────────────────────────────────────────────────────────────

    def _update(self):
        fix = gps_reader.get_fix()
        self.map_view.update_position(fix["lat"], fix["lon"], fix["valid"])

        now = time.time()
        if (fix["valid"] and self.logging
                and self.last_fix_lat is not None
                and now - self.last_log_time >= 1.0):

            self.map_view.paint_swath(
                self.last_fix_lat, self.last_fix_lon,
                fix["lat"],        fix["lon"],
                self.spread_width_m,
            )
            self._write_log(fix)
            self.record_count += 1
            self.last_log_time = now

        if fix["valid"]:
            self.last_fix_lat = fix["lat"]
            self.last_fix_lon = fix["lon"]

        # Auto-dismiss confirm dialog after 5 s.
        if self.confirm_clear and now - self.confirm_timer > 5:
            self.confirm_clear = False

    # ── Draw ──────────────────────────────────────────────────────────────────

    def _draw(self):
        # Map.
        map_surf = self.map_view.render()
        self.screen.blit(map_surf, self.map_rect.topleft)

        # HUD background.
        pygame.draw.rect(self.screen, C_HUD_BG, self.hud_rect)
        pygame.draw.line(self.screen, (60, 60, 60),
                         self.hud_rect.topleft, self.hud_rect.topright, 2)

        self._draw_hud()

        if self.confirm_clear:
            self._draw_confirm_overlay()

    def _draw_hud(self):
        fix = gps_reader.get_fix()

        # ── Start / Stop button ───────────────────────────────────────────────
        btn_colour = C_BTN_STOP if self.logging else C_BTN_START
        btn_label  = "STOP"     if self.logging else "START"
        pygame.draw.rect(self.screen, btn_colour, self.btn_log_rect, border_radius=6)
        lbl = self.font_lg.render(btn_label, True, C_BTN_TEXT)
        self.screen.blit(lbl, lbl.get_rect(center=self.btn_log_rect.center))

        # ── Clear button ──────────────────────────────────────────────────────
        pygame.draw.rect(self.screen, C_BTN_CLEAR, self.btn_clear_rect, border_radius=6)
        clr = self.font_sm.render("CLEAR", True, C_BTN_TEXT)
        self.screen.blit(clr, clr.get_rect(center=self.btn_clear_rect.center))

        # ── Width slider ──────────────────────────────────────────────────────
        sr = self.slider_rail_rect
        pygame.draw.rect(self.screen, C_SLIDER_BG, sr, border_radius=4)

        frac   = (self.spread_width_m - MIN_WIDTH_M) / (MAX_WIDTH_M - MIN_WIDTH_M)
        fill_w = int(frac * sr.width)
        fill_r = pygame.Rect(sr.x, sr.y, fill_w, sr.height)
        pygame.draw.rect(self.screen, C_SLIDER_FG, fill_r, border_radius=4)

        knob_x = sr.x + fill_w
        knob_r = pygame.Rect(knob_x - 8, sr.y - 6, 16, sr.height + 12)
        pygame.draw.rect(self.screen, C_SLIDER_KNB, knob_r, border_radius=4)

        wlbl = self.font_sm.render(f"Width: {self.spread_width_m:.1f} m", True, C_TEXT)
        self.screen.blit(wlbl, (sr.x, sr.y - 22))

        # ── Status info ───────────────────────────────────────────────────────
        sx = self.status_x
        sy = self.hud_rect.y + 6

        fix_colour = C_FIX_YES if fix["valid"] else C_FIX_NO
        fix_text   = "GPS OK" if fix["valid"] else "NO FIX"
        self.screen.blit(self.font_sm.render(fix_text, True, fix_colour), (sx, sy))

        sess_text = f"S{self.session_num:03d}  {self.record_count} pts" if self.logging else "Not logging"
        self.screen.blit(self.font_sm.render(sess_text, True, C_TEXT), (sx, sy + 22))

        if fix["valid"] and fix["timestamp"]:
            ts = fix["timestamp"][11:19]   # HH:MM:SS
            self.screen.blit(self.font_xs.render(ts + " UTC", True, C_TEXT), (sx, sy + 42))

    def _draw_confirm_overlay(self):
        overlay = pygame.Surface((self.W, self.H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        self.screen.blit(overlay, (0, 0))

        box = pygame.Rect(self.W // 2 - 180, self.H // 2 - 60, 360, 120)
        pygame.draw.rect(self.screen, C_WARN_BG, box, border_radius=10)
        pygame.draw.rect(self.screen, (255, 200, 0), box, 3, border_radius=10)

        t1 = self.font_lg.render("Clear coverage?", True, (255, 255, 255))
        self.screen.blit(t1, t1.get_rect(centerx=self.W // 2, y=box.y + 10))

        yes_r = pygame.Rect(box.x + 20,       box.y + 60, 140, 44)
        no_r  = pygame.Rect(box.x + 200,      box.y + 60, 140, 44)
        pygame.draw.rect(self.screen, (180, 40, 40), yes_r, border_radius=6)
        pygame.draw.rect(self.screen, (60, 120, 60), no_r,  border_radius=6)
        self.screen.blit(self.font_lg.render("YES", True, (255,255,255)),
                         self.font_lg.render("YES",True,(0,0,0)).get_rect(center=yes_r.center))
        self.screen.blit(self.font_lg.render("NO",  True, (255,255,255)),
                         self.font_lg.render("NO", True,(0,0,0)).get_rect(center=no_r.center))
        self._confirm_yes_rect = yes_r
        self._confirm_no_rect  = no_r

    # ── Event handling ────────────────────────────────────────────────────────

    def _handle_event(self, event):
        if event.type == pygame.QUIT:
            self._shutdown()

        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._shutdown()

        if event.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
            pos = _touch_pos(event, self.W, self.H)
            self._on_touch_down(pos)

        if event.type in (pygame.MOUSEMOTION, pygame.FINGERMOTION):
            if self._slider_dragging:
                pos = _touch_pos(event, self.W, self.H)
                self._update_slider(pos[0])

        if event.type in (pygame.MOUSEBUTTONUP, pygame.FINGERUP):
            self._slider_dragging = False

    def _on_touch_down(self, pos):
        if self.confirm_clear:
            if hasattr(self, "_confirm_yes_rect") and self._confirm_yes_rect.collidepoint(pos):
                self.map_view.clear_coverage()
                self.record_count = 0
            self.confirm_clear = False
            return

        if self.btn_log_rect.collidepoint(pos):
            self._toggle_logging()
            return

        if self.btn_clear_rect.collidepoint(pos):
            self.confirm_clear = True
            self.confirm_timer = time.time()
            return

        # Expand the slider hit area vertically for fat fingers.
        sr = self.slider_rail_rect
        hit = pygame.Rect(sr.x - 10, sr.y - 20, sr.width + 20, sr.height + 40)
        if hit.collidepoint(pos):
            self._slider_dragging = True
            self._update_slider(pos[0])

    def _update_slider(self, x: int):
        sr = self.slider_rail_rect
        frac = max(0.0, min(1.0, (x - sr.x) / sr.width))
        raw  = MIN_WIDTH_M + frac * (MAX_WIDTH_M - MIN_WIDTH_M)
        # Snap to 0.1 m steps.
        self.spread_width_m = round(raw * 10) / 10

    # ── Logging ───────────────────────────────────────────────────────────────

    def _toggle_logging(self):
        if not self.logging:
            os.makedirs(self.log_dir, exist_ok=True)
            self.session_num  = _next_session(self.log_dir)
            self.record_count = 0
            self.last_log_time = 0
            path = os.path.join(self.log_dir, f"LOG{self.session_num:03d}.CSV")
            self.log_file = open(path, "w", newline="")
            self.log_file.write("timestamp,lat,lon,speed_kmh\n")
            self.logging = True
            print(f"Logging to {path}")
        else:
            self.logging = False
            if self.log_file:
                self.log_file.close()
                self.log_file = None

    def _write_log(self, fix: dict):
        if self.log_file:
            self.log_file.write(
                f"{fix['timestamp']},{fix['lat']:.7f},"
                f"{fix['lon']:.7f},{fix['speed_kmh']:.2f}\n"
            )
            self.log_file.flush()

    # ── HUD layout ────────────────────────────────────────────────────────────

    def _layout_hud(self):
        h = self.hud_rect
        btn_w, btn_h = 110, 52
        pad = 10

        self.btn_log_rect = pygame.Rect(
            h.x + pad, h.y + (h.height - btn_h) // 2, btn_w, btn_h)

        self.btn_clear_rect = pygame.Rect(
            h.x + pad * 2 + btn_w, h.y + (h.height - btn_h) // 2, 80, btn_h)

        slider_x = h.x + pad * 3 + btn_w + 80 + 10
        slider_w = self.W - slider_x - 200 - pad * 2
        slider_h = 16
        self.slider_rail_rect = pygame.Rect(
            slider_x,
            h.y + h.height // 2 + 6,
            max(slider_w, 60),
            slider_h,
        )

        self.status_x = self.W - 195

    # ── Shutdown ──────────────────────────────────────────────────────────────

    def _shutdown(self):
        if self.log_file:
            self.log_file.close()
        pygame.quit()
        sys.exit()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_boundary(path: str) -> list[tuple]:
    pts = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            try:
                pts.append((float(row["lat"]), float(row["lon"])))
            except (KeyError, ValueError):
                continue
    return pts


def _next_session(log_dir: str) -> int:
    n = 1
    while os.path.exists(os.path.join(log_dir, f"LOG{n:03d}.CSV")):
        n += 1
    return n


def _touch_pos(event, W, H) -> tuple[int, int]:
    """Normalise mouse and touch events to screen pixel coordinates."""
    if hasattr(event, "x") and event.x <= 1.0:
        # Normalised finger coordinates.
        return (int(event.x * W), int(event.y * H))
    if hasattr(event, "pos"):
        return event.pos
    return (0, 0)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live spreader coverage map")
    parser.add_argument("--boundary",   default="BOUNDARY.CSV",
                        help="Path to BOUNDARY.CSV (from 'python ../app/main.py boundary')")
    parser.add_argument("--gps-port",   default="/dev/serial0",
                        help="Serial port for L76X GPS")
    parser.add_argument("--log-dir",    default="logs",
                        help="Directory to save session CSV files")
    parser.add_argument("--fullscreen", action="store_true",
                        help="Run fullscreen (recommended for Pi touchscreen)")
    args = parser.parse_args()

    app = SpreaderMapper(
        boundary_csv = args.boundary,
        gps_port     = args.gps_port,
        log_dir      = args.log_dir,
        fullscreen   = args.fullscreen,
    )
    app.run()
