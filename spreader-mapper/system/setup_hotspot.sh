#!/bin/bash
# setup_hotspot.sh - Configure Raspberry Pi Zero 2W as a WiFi access point.
#
# Creates a WiFi hotspot so your iPhone can connect directly without a router.
# The Pi will be accessible at http://192.168.4.1:5000
#
# Run as root: sudo bash setup_hotspot.sh

set -e

# ============================================================
# Configuration - change these as needed
# ============================================================
SSID="SpreaderMap"
PASSWORD="spreader1"
PI_IP="192.168.4.1"
DHCP_START="192.168.4.10"
DHCP_END="192.168.4.50"
INTERFACE="wlan0"
# ============================================================

echo "============================================================"
echo "  Spreader Mapper - WiFi Hotspot Setup"
echo "  SSID: $SSID"
echo "  Password: $PASSWORD"
echo "  Pi IP: $PI_IP"
echo "============================================================"
echo ""

# Must be root
if [ "$EUID" -ne 0 ]; then
  echo "Error: Please run as root (sudo bash setup_hotspot.sh)"
  exit 1
fi

# Install required packages
echo "[1/6] Installing hostapd and dnsmasq..."
apt-get update -qq
apt-get install -y -qq hostapd dnsmasq

# Stop services before configuring
echo "[2/6] Stopping services for configuration..."
systemctl stop hostapd 2>/dev/null || true
systemctl stop dnsmasq 2>/dev/null || true

# ============================================================
# Configure hostapd (the access point daemon)
# ============================================================
echo "[3/6] Writing /etc/hostapd/hostapd.conf..."
cat > /etc/hostapd/hostapd.conf << HOSTAPD_EOF
# hostapd.conf - Written by spreader-mapper setup_hotspot.sh
interface=$INTERFACE
driver=nl80211
ssid=$SSID
hw_mode=g
channel=7
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=$PASSWORD
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP
rsn_pairwise=CCMP
HOSTAPD_EOF

# Tell hostapd where its config file lives
if [ -f /etc/default/hostapd ]; then
    # Update DAEMON_CONF line
    sed -i 's|^#\?DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd
else
    echo 'DAEMON_CONF="/etc/hostapd/hostapd.conf"' > /etc/default/hostapd
fi

# ============================================================
# Configure dnsmasq (DHCP server for connected clients)
# ============================================================
echo "[4/6] Writing /etc/dnsmasq.conf..."

# Back up original config if it exists and hasn't been backed up already
if [ -f /etc/dnsmasq.conf ] && [ ! -f /etc/dnsmasq.conf.original ]; then
    cp /etc/dnsmasq.conf /etc/dnsmasq.conf.original
    echo "  Backed up original dnsmasq.conf to /etc/dnsmasq.conf.original"
fi

cat > /etc/dnsmasq.conf << DNSMASQ_EOF
# dnsmasq.conf - Written by spreader-mapper setup_hotspot.sh
# Provides DHCP for WiFi clients on $INTERFACE

interface=$INTERFACE
dhcp-range=$DHCP_START,$DHCP_END,255.255.255.0,24h

# Redirect all DNS to the Pi itself so http://spreadermap/ works
# (optional - mainly useful if you add a hostname)
address=/spreadermap/$PI_IP

# Don't use /etc/hosts for DNS forwarding
no-hosts

# Log DHCP assignments (useful for debugging)
log-dhcp
DNSMASQ_EOF

# ============================================================
# Configure static IP for wlan0
# ============================================================
echo "[5/6] Configuring static IP $PI_IP for $INTERFACE..."

# dhcpcd.conf - add static IP config for wlan0 if not already present
DHCPCD_CONF="/etc/dhcpcd.conf"
STATIC_BLOCK="# spreader-mapper: static IP for hotspot
interface $INTERFACE
    static ip_address=$PI_IP/24
    nohook wpa_supplicant"

if grep -q "spreader-mapper" "$DHCPCD_CONF" 2>/dev/null; then
    echo "  (static IP block already present in dhcpcd.conf)"
else
    echo "" >> "$DHCPCD_CONF"
    echo "$STATIC_BLOCK" >> "$DHCPCD_CONF"
    echo "  Added static IP block to $DHCPCD_CONF"
fi

# ============================================================
# Enable and start services
# ============================================================
echo "[6/6] Enabling services..."

# Unmask hostapd (it's masked by default on some Raspberry Pi OS versions)
systemctl unmask hostapd
systemctl enable hostapd
systemctl enable dnsmasq

# Enable IP forwarding (not strictly needed for our use case, but good practice)
if ! grep -q "^net.ipv4.ip_forward=1" /etc/sysctl.conf; then
    echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
fi

echo ""
echo "============================================================"
echo "  Setup complete!"
echo ""
echo "  IMPORTANT NOTES:"
echo "  - The Pi will lose internet access on $INTERFACE (wlan0)"
echo "    You can still use eth0 or USB for internet during setup"
echo "  - A reboot is required for all changes to take effect"
echo ""
echo "  After reboot:"
echo "    1. Connect your iPhone to WiFi: $SSID"
echo "       Password: $PASSWORD"
echo "    2. Open Safari and go to: http://$PI_IP:5000"
echo ""
echo "  To reboot now: sudo reboot"
echo "============================================================"
