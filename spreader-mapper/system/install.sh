#!/bin/bash
# install.sh - Complete installation script for Spreader Mapper on Raspberry Pi OS Lite.
#
# Run from the spreader-mapper directory:
#   cd /home/pi/spreader-mapper
#   sudo bash system/install.sh

set -e

INSTALL_DIR="/home/pi/spreader-mapper"
BACKEND_DIR="$INSTALL_DIR/backend"
FRONTEND_DIR="$INSTALL_DIR/frontend"
SYSTEM_DIR="$INSTALL_DIR/system"

echo "============================================================"
echo "  Spreader Mapper - Installation"
echo "  Install directory: $INSTALL_DIR"
echo "============================================================"
echo ""

# Must be root
if [ "$EUID" -ne 0 ]; then
  echo "Error: Please run as root (sudo bash system/install.sh)"
  exit 1
fi

# Verify we're in the right place
if [ ! -f "$BACKEND_DIR/app.py" ]; then
    echo "Error: Cannot find $BACKEND_DIR/app.py"
    echo "Please run from the spreader-mapper directory or ensure files are in $INSTALL_DIR"
    exit 1
fi

# ============================================================
# Step 1: System packages
# ============================================================
echo "[1/11] Installing system packages..."
apt-get update -qq
apt-get install -y -qq \
    python3-pip \
    python3-dev \
    python3-setuptools \
    hostapd \
    dnsmasq \
    git \
    curl \
    wget \
    libgeos-dev \
    libproj-dev
echo "  Done."

# ============================================================
# Step 2: Python dependencies
# ============================================================
echo "[2/11] Installing Python packages (this may take several minutes on Pi Zero 2W)..."
pip3 install -r "$BACKEND_DIR/requirements.txt" --break-system-packages 2>/dev/null \
    || pip3 install -r "$BACKEND_DIR/requirements.txt"
echo "  Done."

# ============================================================
# Step 3: Create frontend JS/CSS directory structure
# ============================================================
echo "[3/11] Creating frontend asset directories..."
mkdir -p "$FRONTEND_DIR/js"
mkdir -p "$FRONTEND_DIR/css/images"
echo "  Done."

# ============================================================
# Step 4: Download Leaflet.js v1.9.4
# ============================================================
echo "[4/11] Downloading Leaflet.js v1.9.4..."
LEAFLET_BASE="https://unpkg.com/leaflet@1.9.4/dist"

if [ ! -f "$FRONTEND_DIR/js/leaflet.js" ]; then
    wget -q -O "$FRONTEND_DIR/js/leaflet.js" "$LEAFLET_BASE/leaflet.js"
    echo "  Downloaded leaflet.js"
else
    echo "  leaflet.js already present, skipping"
fi

if [ ! -f "$FRONTEND_DIR/css/leaflet.css" ]; then
    wget -q -O "$FRONTEND_DIR/css/leaflet.css" "$LEAFLET_BASE/leaflet.css"
    echo "  Downloaded leaflet.css"
else
    echo "  leaflet.css already present, skipping"
fi

# ============================================================
# Step 5: Download Socket.IO client v4.7.5
# ============================================================
echo "[5/11] Downloading Socket.IO client v4.7.5..."
SOCKETIO_URL="https://cdn.socket.io/4.7.5/socket.io.min.js"

if [ ! -f "$FRONTEND_DIR/js/socket.io.min.js" ]; then
    wget -q -O "$FRONTEND_DIR/js/socket.io.min.js" "$SOCKETIO_URL"
    echo "  Downloaded socket.io.min.js"
else
    echo "  socket.io.min.js already present, skipping"
fi

# ============================================================
# Step 6: Download Leaflet marker images
# ============================================================
echo "[6/11] Downloading Leaflet marker images..."
IMG_BASE="https://unpkg.com/leaflet@1.9.4/dist/images"

for img in marker-icon.png marker-icon-2x.png marker-shadow.png layers.png layers-2x.png; do
    if [ ! -f "$FRONTEND_DIR/css/images/$img" ]; then
        wget -q -O "$FRONTEND_DIR/css/images/$img" "$IMG_BASE/$img" && echo "  Downloaded $img" || echo "  Warning: could not download $img"
    fi
done

# ============================================================
# Step 7: Create data directories
# ============================================================
echo "[7/11] Creating data directories..."
mkdir -p "$BACKEND_DIR/data/boundaries"
mkdir -p "$BACKEND_DIR/data/sessions"
chown -R pi:pi "$BACKEND_DIR/data"
echo "  Done."

# ============================================================
# Step 8: Enable UART for GPS module
# ============================================================
echo "[8/11] Enabling UART for GPS module..."
CONFIG_FILE="/boot/firmware/config.txt"
if [ ! -f "$CONFIG_FILE" ]; then
    CONFIG_FILE="/boot/config.txt"  # older Pi OS path
fi

if grep -q "enable_uart=1" "$CONFIG_FILE" 2>/dev/null; then
    echo "  UART already enabled in $CONFIG_FILE"
else
    echo "enable_uart=1" >> "$CONFIG_FILE"
    echo "  Added enable_uart=1 to $CONFIG_FILE"
fi

# Disable serial console on ttyS0 (so GPS can use it)
CMDLINE_FILE="/boot/firmware/cmdline.txt"
if [ ! -f "$CMDLINE_FILE" ]; then
    CMDLINE_FILE="/boot/cmdline.txt"
fi

if grep -q "console=serial0" "$CMDLINE_FILE" 2>/dev/null; then
    sed -i 's/console=serial0,[0-9]* //' "$CMDLINE_FILE"
    echo "  Removed serial console from $CMDLINE_FILE"
else
    echo "  Serial console not in $CMDLINE_FILE, no change needed"
fi

# Disable Bluetooth to free up the better UART (only on Pi 3/Zero W/2W)
if ! grep -q "dtoverlay=disable-bt" "$CONFIG_FILE" 2>/dev/null; then
    echo "dtoverlay=disable-bt" >> "$CONFIG_FILE"
    echo "  Disabled Bluetooth overlay to free hardware UART"
fi

# ============================================================
# Step 9: Add pi to dialout group (serial port access)
# ============================================================
echo "[9/11] Adding pi user to dialout group..."
usermod -a -G dialout pi
echo "  Done. Will take effect after reboot."

# ============================================================
# Step 10: Install systemd service
# ============================================================
echo "[10/11] Installing systemd service..."
cp "$SYSTEM_DIR/spreader.service" /etc/systemd/system/spreader.service
systemctl daemon-reload
systemctl enable spreader.service
echo "  Service installed and enabled."

# ============================================================
# Step 11: Configure WiFi hotspot
# ============================================================
echo "[11/11] Configuring WiFi hotspot..."
bash "$SYSTEM_DIR/setup_hotspot.sh"

# ============================================================
# Set ownership
# ============================================================
chown -R pi:pi "$INSTALL_DIR"

echo ""
echo "============================================================"
echo "  Installation complete!"
echo ""
echo "  NEXT STEPS:"
echo "  1. Reboot the Pi:  sudo reboot"
echo "  2. Connect your iPhone to WiFi: SpreaderMap (password: spreader1)"
echo "  3. Open Safari → http://192.168.4.1:5000"
echo ""
echo "  OPTIONAL - Fetch your property boundary while you have internet:"
echo "    cd $INSTALL_DIR/tools"
echo "    python3 fetch_boundary.py --address \"123 Main St, City, ST\" \\"
echo "        --out ../backend/data/boundaries/boundary.geojson"
echo ""
echo "  To check service status after reboot:"
echo "    sudo journalctl -u spreader -f"
echo "============================================================"
