/**
 * map.js - Leaflet + Socket.IO frontend for the GPS Spreader Mapper.
 *
 * Connects to the backend via WebSocket, renders map layers in real-time,
 * and handles all UI interactions.
 */

'use strict';

const mapApp = (() => {
  // -------------------------------------------------------------------------
  // State
  // -------------------------------------------------------------------------
  let map;
  let socket;

  // Layers
  let positionMarker = null;
  let positionRing = null;
  let widthBand = null;
  let coverageLayer = null;
  let trackPolyline = null;
  let currentSwathLayer = null;
  let boundaryLayer = null;
  let guideLayer = null;

  // UI state
  let followMode = true;
  let sessionActive = false;
  let sessionStartTime = null;  // epoch ms
  let durationTimer = null;

  // Local shadow of settings (updated optimistically on button press)
  let currentWidthFt = 12;
  let currentSpacingFt = 10;
  let currentSpreading = false;
  let currentPaused = false;

  // Track points for polyline (lon, lat) → Leaflet [lat, lon]
  let trackPoints = [];

  // Boundary bounds for fit
  let boundaryBounds = null;

  // -------------------------------------------------------------------------
  // Initialization
  // -------------------------------------------------------------------------

  function init() {
    initMap();
    initSocket();
    startDurationTimer();
    loadBoundary();
  }

  function initMap() {
    map = L.map('map', {
      center: [39.5, -98.35],  // center of US - overridden by boundary/position
      zoom: 18,
      zoomControl: true,
      attributionControl: false,
    });

    // Tile layer - works offline if cached; use OpenStreetMap otherwise
    // The install.sh script should cache tiles, but for initial use:
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 22,
      attribution: '© OpenStreetMap contributors',
    }).addTo(map);

    map.on('dragstart', () => {
      followMode = false;
      updateFollowButton();
    });
  }

  function initSocket() {
    socket = io({
      transports: ['websocket'],
      reconnectionDelay: 1000,
      reconnectionAttempts: Infinity,
    });

    socket.on('connect', () => {
      console.log('Socket connected');
    });

    socket.on('disconnect', () => {
      console.log('Socket disconnected');
    });

    socket.on('state', handleState);
  }

  // -------------------------------------------------------------------------
  // State handler
  // -------------------------------------------------------------------------

  function handleState(state) {
    updatePosition(state.position);
    updateSessionUI(state);
    updateSpreadingUI(state.spreading, state.paused);
    updateSettings(state.spread_width_ft, state.pass_spacing_ft);
    updateCoverage(state.coverage_geojson);
    updateTrack(state.track_geojson);
    updateCurrentSwath(state.current_swath_geojson);
    updateOverlapWarning(state.overlap_fraction);
    updateStats(state.stats, state.session_active);

    // Track session start time for duration display
    if (state.session_active && sessionStartTime === null) {
      sessionStartTime = Date.now() - ((state.stats.duration_seconds || 0) * 1000);
    }
    if (!state.session_active) {
      sessionStartTime = null;
    }

    sessionActive = state.session_active;
    currentSpreading = state.spreading;
    currentPaused = state.paused;
    currentWidthFt = state.spread_width_ft;
    currentSpacingFt = state.pass_spacing_ft;
  }

  // -------------------------------------------------------------------------
  // Position
  // -------------------------------------------------------------------------

  function updatePosition(pos) {
    if (!pos || !pos.valid || pos.lat == null || pos.lon == null) {
      // Show no-GPS overlay
      document.getElementById('no-gps-overlay').classList.remove('hidden');
      document.getElementById('gps-dot').className = 'dot dot-red';
      document.getElementById('gps-label').textContent = 'No Fix';

      const sats = pos ? pos.satellites : 0;
      document.getElementById('no-gps-sats').textContent =
        sats > 0 ? `${sats} satellite${sats !== 1 ? 's' : ''} in view` : 'Searching…';
      return;
    }

    // Hide no-GPS overlay
    document.getElementById('no-gps-overlay').classList.add('hidden');

    // Status bar
    const quality = pos.fix_quality;
    const dotClass = quality >= 2 ? 'dot-green' : (quality === 1 ? 'dot-green' : 'dot-orange');
    document.getElementById('gps-dot').className = `dot ${dotClass}`;
    document.getElementById('gps-label').textContent =
      quality >= 2 ? 'DGPS' : (quality === 1 ? 'GPS Fix' : 'Fix');
    document.getElementById('sat-count').textContent = `${pos.satellites} sats`;
    document.getElementById('hdop-val').textContent = `HDOP ${pos.hdop != null ? pos.hdop.toFixed(1) : '--'}`;

    if (pos.timestamp) {
      // Extract time portion from ISO string
      const t = pos.timestamp.replace('Z', '').split('T')[1] || '';
      document.getElementById('utc-time').textContent = t || '--:--:--';
    }

    const latlng = [pos.lat, pos.lon];

    // Width band — pixel-radius ring that scales with spread width so it's
    // always visible regardless of zoom level (widthFt * 1.8 px, min 18px)
    const bandPx = Math.max(18, Math.round(currentWidthFt * 1.8));
    if (widthBand) {
      widthBand.setLatLng(latlng);
      widthBand.setRadius(bandPx);
    } else {
      widthBand = L.circleMarker(latlng, {
        radius: bandPx,
        fillColor: '#ffeb3b',
        fillOpacity: 0.25,
        color: '#f57f17',
        weight: 3,
        interactive: false,
      }).addTo(map);
    }

    // Position marker
    if (positionMarker) {
      positionMarker.setLatLng(latlng);
    } else {
      positionMarker = L.circleMarker(latlng, {
        radius: 8,
        fillColor: '#76ff03',
        fillOpacity: 1,
        color: '#fff',
        weight: 2,
        className: '',
      }).addTo(map);
    }

    // Accuracy ring (pulsing)
    if (positionRing) {
      positionRing.setLatLng(latlng);
    } else {
      positionRing = L.circleMarker(latlng, {
        radius: 16,
        fillColor: '#76ff03',
        fillOpacity: 0.15,
        color: '#76ff03',
        weight: 1,
        className: 'position-pulse-ring',
      }).addTo(map);
    }

    if (followMode) {
      map.panTo(latlng, { animate: true, duration: 0.5 });
    }
  }

  // -------------------------------------------------------------------------
  // Coverage layer
  // -------------------------------------------------------------------------

  function updateCoverage(geojson) {
    if (coverageLayer) {
      map.removeLayer(coverageLayer);
      coverageLayer = null;
    }
    if (!geojson) return;

    coverageLayer = L.geoJSON(geojson, {
      style: {
        fillColor: '#4caf50',
        fillOpacity: 0.35,
        color: '#4caf50',
        weight: 1,
      },
    }).addTo(map);
  }

  // -------------------------------------------------------------------------
  // Track polyline
  // -------------------------------------------------------------------------

  function updateTrack(geojson) {
    if (!geojson) {
      if (trackPolyline) {
        map.removeLayer(trackPolyline);
        trackPolyline = null;
      }
      return;
    }

    // Extract coordinates from GeoJSON LineString
    let coords = [];
    const geom = geojson.geometry || geojson;
    if (geom && geom.type === 'LineString' && geom.coordinates) {
      coords = geom.coordinates.map(c => [c[1], c[0]]);  // [lon,lat] → [lat,lon]
    }

    if (coords.length < 2) return;

    if (trackPolyline) {
      trackPolyline.setLatLngs(coords);
    } else {
      trackPolyline = L.polyline(coords, {
        color: '#ffffffcc',
        weight: 2,
        opacity: 0.7,
      }).addTo(map);
    }
  }

  // -------------------------------------------------------------------------
  // Current swath
  // -------------------------------------------------------------------------

  function updateCurrentSwath(geojson) {
    if (currentSwathLayer) {
      map.removeLayer(currentSwathLayer);
      currentSwathLayer = null;
    }
    if (!geojson) return;

    currentSwathLayer = L.geoJSON(geojson, {
      style: {
        fillColor: '#ffeb3b',
        fillOpacity: 0.55,
        color: '#f57f17',
        weight: 2,
      },
    }).addTo(map);
  }

  // -------------------------------------------------------------------------
  // Boundary layer
  // -------------------------------------------------------------------------

  function loadBoundary() {
    fetch('/api/boundary')
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!data) return;
        renderBoundary(data);
        loadGuideLines();
      })
      .catch(() => {});
  }

  function renderBoundary(geojson) {
    if (boundaryLayer) {
      map.removeLayer(boundaryLayer);
    }

    boundaryLayer = L.geoJSON(geojson, {
      style: {
        fillColor: 'transparent',
        fillOpacity: 0,
        color: '#2196f3',
        weight: 2,
        dashArray: '8 4',
      },
    }).addTo(map);

    boundaryBounds = boundaryLayer.getBounds();
    if (boundaryBounds.isValid()) {
      map.fitBounds(boundaryBounds, { padding: [20, 20] });
    }
  }

  // -------------------------------------------------------------------------
  // Guide lines
  // -------------------------------------------------------------------------

  function loadGuideLines() {
    fetch('/api/guide_lines')
      .then(r => r.json())
      .then(data => renderGuideLines(data))
      .catch(() => {});
  }

  function renderGuideLines(geojson) {
    if (guideLayer) {
      map.removeLayer(guideLayer);
      guideLayer = null;
    }
    if (!geojson || !geojson.features || geojson.features.length === 0) return;

    guideLayer = L.geoJSON(geojson, {
      style: {
        color: '#555',
        weight: 1,
        dashArray: '6 4',
        opacity: 0.6,
      },
    }).addTo(map);
  }

  // -------------------------------------------------------------------------
  // Overlap warning
  // -------------------------------------------------------------------------

  function updateOverlapWarning(fraction) {
    const threshold = 0.15;
    const warning = document.getElementById('overlap-warning');
    if (fraction > threshold) {
      warning.classList.remove('hidden');
      document.body.classList.add('overlap-active');
    } else {
      warning.classList.add('hidden');
      document.body.classList.remove('overlap-active');
    }
  }

  // -------------------------------------------------------------------------
  // Session UI
  // -------------------------------------------------------------------------

  function updateSessionUI(state) {
    const btnSession = document.getElementById('btn-session');
    const btnPause = document.getElementById('btn-pause');

    if (state.session_active) {
      btnSession.textContent = '⏹ END SESSION';
      btnSession.className = 'btn btn-stop';
      btnPause.disabled = false;
    } else {
      btnSession.textContent = '▶ START SESSION';
      btnSession.className = 'btn btn-start';
      btnPause.disabled = true;
    }
  }

  // -------------------------------------------------------------------------
  // Spreading / pause UI
  // -------------------------------------------------------------------------

  function updateSpreadingUI(spreading, paused) {
    const btnSpreading = document.getElementById('btn-spreading');
    const btnPause = document.getElementById('btn-pause');

    if (spreading) {
      btnSpreading.textContent = '🌱 SPREADING ON';
      btnSpreading.className = 'btn btn-spreading-on btn-wide';
    } else {
      btnSpreading.textContent = '⬜ SPREADING OFF';
      btnSpreading.className = 'btn btn-spreading-off btn-wide';
    }

    if (paused) {
      btnPause.textContent = '▶ RESUME';
      btnPause.className = 'btn btn-start';
    } else {
      btnPause.textContent = '⏸ PAUSE';
      btnPause.className = 'btn btn-pause';
    }
  }

  // -------------------------------------------------------------------------
  // Settings display
  // -------------------------------------------------------------------------

  function updateSettings(widthFt, spacingFt) {
    document.getElementById('width-val').textContent = `${widthFt} ft`;
    document.getElementById('spacing-val').textContent = `${spacingFt} ft`;
    if (widthBand) {
      widthBand.setRadius(Math.max(18, Math.round(widthFt * 1.8)));
    }
  }

  // -------------------------------------------------------------------------
  // Stats display
  // -------------------------------------------------------------------------

  function updateStats(stats, active) {
    if (!stats || !active) {
      document.getElementById('stat-area').textContent = 'Area: 0.00 ac';
      document.getElementById('stat-coverage').textContent = '0% covered';
      return;
    }

    const acres = stats.area_acres || 0;
    const sqft  = stats.area_sqft || 0;

    let areaText;
    if (acres >= 0.1) {
      areaText = `Area: ${acres.toFixed(2)} ac`;
    } else {
      areaText = `Area: ${Math.round(sqft)} sqft`;
    }

    document.getElementById('stat-area').textContent = areaText;
    document.getElementById('stat-coverage').textContent =
      stats.pct_coverage != null
        ? `${stats.pct_coverage.toFixed(1)}% covered`
        : '-- covered';
  }

  // -------------------------------------------------------------------------
  // Duration timer
  // -------------------------------------------------------------------------

  function startDurationTimer() {
    durationTimer = setInterval(() => {
      if (!sessionActive || sessionStartTime === null) {
        document.getElementById('stat-duration').textContent = '00:00';
        return;
      }
      const elapsed = Math.floor((Date.now() - sessionStartTime) / 1000);
      const mm = String(Math.floor(elapsed / 60)).padStart(2, '0');
      const ss = String(elapsed % 60).padStart(2, '0');
      const hh = Math.floor(elapsed / 3600);
      document.getElementById('stat-duration').textContent =
        hh > 0 ? `${String(hh).padStart(2,'0')}:${mm}:${ss}` : `${mm}:${ss}`;
    }, 1000);
  }

  // -------------------------------------------------------------------------
  // Button handlers
  // -------------------------------------------------------------------------

  function toggleSession() {
    if (sessionActive) {
      fetch('/api/session/stop', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
          console.log('Session stopped:', data.stats);
          sessionStartTime = null;
        })
        .catch(console.error);
    } else {
      const body = {
        spread_width_ft: currentWidthFt,
        pass_spacing_ft: currentSpacingFt,
      };
      fetch('/api/session/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
        .then(r => r.json())
        .then(() => {
          sessionStartTime = Date.now();
        })
        .catch(console.error);
    }
  }

  function togglePause() {
    fetch('/api/session/pause', { method: 'POST' })
      .then(r => r.json())
      .catch(console.error);
  }

  function toggleSpreading() {
    const newVal = !currentSpreading;
    currentSpreading = newVal;
    // Optimistic update
    updateSpreadingUI(newVal, currentPaused);
    fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spreading: newVal }),
    }).catch(console.error);
  }

  function adjustWidth(delta) {
    const newVal = Math.max(4, Math.min(24, currentWidthFt + delta));
    if (newVal === currentWidthFt) return;
    currentWidthFt = newVal;
    document.getElementById('width-val').textContent = `${newVal} ft`;
    if (widthBand) widthBand.setRadius(Math.max(18, Math.round(newVal * 1.8)));
    fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spread_width_ft: newVal }),
    }).catch(console.error);
  }

  function adjustSpacing(delta) {
    const newVal = Math.max(4, Math.min(30, currentSpacingFt + delta));
    if (newVal === currentSpacingFt) return;
    currentSpacingFt = newVal;
    document.getElementById('spacing-val').textContent = `${newVal} ft`;
    fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pass_spacing_ft: newVal }),
    })
      .then(() => loadGuideLines())  // Refresh guide lines on spacing change
      .catch(console.error);
  }

  function fitBounds() {
    if (boundaryBounds && boundaryBounds.isValid()) {
      map.fitBounds(boundaryBounds, { padding: [20, 20] });
    } else if (positionMarker) {
      map.setView(positionMarker.getLatLng(), 18);
    }
  }

  function toggleFollow() {
    followMode = !followMode;
    updateFollowButton();
    if (followMode && positionMarker) {
      map.panTo(positionMarker.getLatLng());
    }
  }

  function updateFollowButton() {
    // Visual feedback - could add active state styling
    const btns = document.querySelectorAll('.btn-secondary');
    // Find the follow button by text
    btns.forEach(b => {
      if (b.textContent.includes('Follow')) {
        b.style.background = followMode ? '#1976d2' : '';
      }
    });
  }

  function openExport() {
    document.getElementById('export-modal').classList.remove('hidden');
  }

  function closeExport(event) {
    if (!event || event.target === document.getElementById('export-modal') || !event.target.closest('.modal-card').length) {
      document.getElementById('export-modal').classList.add('hidden');
    }
  }

  // -------------------------------------------------------------------------
  // Public API
  // -------------------------------------------------------------------------

  return {
    init,
    toggleSession,
    togglePause,
    toggleSpreading,
    adjustWidth,
    adjustSpacing,
    fitBounds,
    toggleFollow,
    openExport,
    closeExport,
  };

})();

// Boot
document.addEventListener('DOMContentLoaded', () => mapApp.init());
