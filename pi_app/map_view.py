"""
map_view.py
Handles all coordinate math and pygame surface rendering for the live map.

Coordinate system
─────────────────
All geographic data lives in WGS-84 (lat, lon).
The map viewport is a rectangle of screen pixels.
We project lat/lon → pixels using a simple linear (equirectangular) transform
fitted to the bounding box of the property boundary plus a margin.
"""

import math
import pygame


# Colours
C_BG         = (15,  25,  15)   # near-black green background
C_BOUNDARY   = (80, 180, 255)   # blue dashed outline
C_TREATED    = (50, 200,  80)   # green swath fill
C_POSITION   = (255, 255,  60)  # yellow current-position dot
C_TRAIL      = (180, 255, 180)  # light-green centre-line trail
C_GRID       = (30,  40,  30)   # very dark grid lines
C_NO_FIX     = (200,  60,  60)  # red – shown when no GPS fix


class MapView:
    """
    Manages the map layer.  Call in order:
        mv = MapView(rect, boundary_pts)
        mv.update_position(lat, lon)   # from GPS thread
        mv.paint_swath(prev, cur, width_m)  # every new fix while logging
        surface = mv.render()          # call each frame
    """

    def __init__(self, rect: pygame.Rect, boundary_pts: list[tuple]):
        """
        rect          – pygame.Rect defining where on screen to draw the map
        boundary_pts  – list of (lat, lon) tuples for the property boundary
        """
        self.rect = rect
        self.boundary_pts = boundary_pts

        # Fit the viewport to the boundary bounding box + 10 % margin.
        if boundary_pts:
            lats = [p[0] for p in boundary_pts]
            lons = [p[1] for p in boundary_pts]
            lat_pad = (max(lats) - min(lats)) * 0.10 or 0.0002
            lon_pad = (max(lons) - min(lons)) * 0.10 or 0.0002
            self._min_lat = min(lats) - lat_pad
            self._max_lat = max(lats) + lat_pad
            self._min_lon = min(lons) - lon_pad
            self._max_lon = max(lons) + lon_pad
        else:
            # Will be set dynamically once GPS fixes arrive.
            self._min_lat = self._max_lat = None
            self._min_lon = self._max_lon = None

        # The treated-area surface accumulates swath fills and persists
        # between frames.  Alpha channel lets it composite over the background.
        self._coverage = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
        self._coverage.fill((0, 0, 0, 0))

        # Background surface (boundary + grid) – rebuilt when viewport changes.
        self._bg = pygame.Surface((rect.width, rect.height))
        self._bg_dirty = True

        self._cur_lat   = None
        self._cur_lon   = None
        self._has_fix   = False
        self._trail     = []          # list of pixel (x, y) for the GPS trail

    # ── Public API ─────────────────────────────────────────────────────────────

    def update_position(self, lat: float, lon: float, has_fix: bool):
        self._has_fix = has_fix
        if not has_fix:
            return
        if self._min_lat is None:
            self._init_viewport(lat, lon)
        self._cur_lat = lat
        self._cur_lon = lon
        px = self._project(lat, lon)
        if px and (not self._trail or self._trail[-1] != px):
            self._trail.append(px)
            if len(self._trail) > 2000:
                self._trail.pop(0)

    def paint_swath(self, prev_lat: float, prev_lon: float,
                    cur_lat: float,  cur_lon: float,
                    width_m: float):
        """
        Paint a filled rectangle representing the spread swath between two GPS
        fixes onto the persistent coverage surface.
        """
        p1 = self._project(prev_lat, prev_lon)
        p2 = self._project(cur_lat,  cur_lon)
        if p1 is None or p2 is None:
            return

        half_px = self._metres_to_px(width_m / 2, (prev_lat + cur_lat) / 2)
        if half_px < 1:
            half_px = 1

        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        length = math.hypot(dx, dy)
        if length < 0.5:
            pygame.draw.circle(self._coverage, (*C_TREATED, 200),
                               p1, int(half_px))
            return

        # Perpendicular unit vector.
        nx, ny = -dy / length, dx / length

        # Four corners of the swath rectangle.
        corners = [
            (p1[0] + nx * half_px, p1[1] + ny * half_px),
            (p2[0] + nx * half_px, p2[1] + ny * half_px),
            (p2[0] - nx * half_px, p2[1] - ny * half_px),
            (p1[0] - nx * half_px, p1[1] - ny * half_px),
        ]
        pygame.draw.polygon(self._coverage, (*C_TREATED, 200), corners)
        # Round the ends.
        pygame.draw.circle(self._coverage, (*C_TREATED, 200), p1, int(half_px))
        pygame.draw.circle(self._coverage, (*C_TREATED, 200), p2, int(half_px))

    def render(self) -> pygame.Surface:
        """Build and return the fully composited map surface for this frame."""
        if self._bg_dirty:
            self._redraw_bg()

        surf = self._bg.copy()

        # Treated coverage.
        surf.blit(self._coverage, (0, 0))

        # Trail (centre line of where you've walked).
        if len(self._trail) >= 2:
            pygame.draw.lines(surf, C_TRAIL, False, self._trail, 1)

        # Current position.
        if self._cur_lat is not None:
            px = self._project(self._cur_lat, self._cur_lon)
            if px:
                colour = C_POSITION if self._has_fix else C_NO_FIX
                pygame.draw.circle(surf, colour, px, 7)
                pygame.draw.circle(surf, (0, 0, 0), px, 7, 2)  # black outline

        return surf

    def clear_coverage(self):
        """Erase the painted swaths (start of a new session)."""
        self._coverage.fill((0, 0, 0, 0))
        self._trail.clear()

    # ── Internal ───────────────────────────────────────────────────────────────

    def _init_viewport(self, lat: float, lon: float):
        """Bootstrap the viewport around the first GPS fix if no boundary file."""
        d = 0.001   # ~100 m
        self._min_lat = lat - d
        self._max_lat = lat + d
        self._min_lon = lon - d
        self._max_lon = lon + d
        self._bg_dirty = True

    def _project(self, lat: float, lon: float) -> tuple[int, int] | None:
        if self._min_lat is None:
            return None
        lat_r = self._max_lat - self._min_lat
        lon_r = self._max_lon - self._min_lon
        if lat_r == 0 or lon_r == 0:
            return None
        x = int((lon - self._min_lon) / lon_r * (self.rect.width  - 1))
        y = int((self._max_lat - lat) / lat_r * (self.rect.height - 1))
        return (x, y)

    def _metres_to_px(self, metres: float, lat: float) -> int:
        if self._min_lat is None:
            return 5
        R = 6_371_000
        lat_r = self._max_lat - self._min_lat
        lon_r = self._max_lon - self._min_lon
        if lat_r == 0 or lon_r == 0:
            return 5
        deg_per_m_lon = 1.0 / (R * math.cos(math.radians(lat)) * math.pi / 180)
        lon_frac = metres * deg_per_m_lon / lon_r
        return max(1, int(lon_frac * (self.rect.width - 1)))

    def _redraw_bg(self):
        self._bg.fill(C_BG)

        # Subtle grid.
        for x in range(0, self.rect.width, 40):
            pygame.draw.line(self._bg, C_GRID, (x, 0), (x, self.rect.height))
        for y in range(0, self.rect.height, 40):
            pygame.draw.line(self._bg, C_GRID, (0, y), (self.rect.width, y))

        # Property boundary as a dashed blue polygon.
        if len(self.boundary_pts) >= 2:
            pts = [self._project(lat, lon)
                   for lat, lon in self.boundary_pts]
            pts = [p for p in pts if p is not None]
            if len(pts) >= 2:
                _draw_dashed_polygon(self._bg, C_BOUNDARY, pts, dash_len=8, gap_len=5)

        self._bg_dirty = False


# ── Dashed polygon helper ─────────────────────────────────────────────────────

def _draw_dashed_polygon(surf, colour, pts, dash_len=8, gap_len=4):
    n = len(pts)
    for i in range(n):
        p1 = pts[i]
        p2 = pts[(i + 1) % n]
        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        seg_len = math.hypot(dx, dy)
        if seg_len == 0:
            continue
        nx, ny = dx / seg_len, dy / seg_len
        t = 0.0
        draw = True
        while t < seg_len:
            step = dash_len if draw else gap_len
            t2 = min(t + step, seg_len)
            if draw:
                x1 = int(p1[0] + t  * nx)
                y1 = int(p1[1] + t  * ny)
                x2 = int(p1[0] + t2 * nx)
                y2 = int(p1[1] + t2 * ny)
                pygame.draw.line(surf, colour, (x1, y1), (x2, y2), 2)
            t = t2
            draw = not draw
