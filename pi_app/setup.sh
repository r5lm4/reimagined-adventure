#!/bin/bash
# Run once on the Raspberry Pi after cloning the repo.
# Sets up pygame, serial, enables the hardware UART, and creates a systemd
# service so the mapper starts automatically on boot.

set -e

echo "=== Spreader Mapper – Pi setup ==="

# 1. System packages
sudo apt-get update -qq
sudo apt-get install -y python3-pip python3-pygame python3-serial fonts-dejavu

# 2. Python packages
pip3 install --break-system-packages -r "$(dirname "$0")/requirements.txt"

# 3. Enable hardware UART (disables Bluetooth on Pi 3/4; safe for this project)
BOOT_CFT=/boot/firmware/config.txt
[ -f "$BOOT_CFT" ] || BOOT_CFT=/boot/config.txt
if ! grep -q "enable_uart=1" "$BOOT_CFT"; then
    echo "enable_uart=1" | sudo tee -a "$BOOT_CFT"
    echo "dtoverlay=disable-bt" | sudo tee -a "$BOOT_CFT"
    echo "UART enabled in $BOOT_CFT – reboot required"
fi

# 4. Add current user to dialout group (serial port access)
sudo usermod -aG dialout "$USER"

# 5. Disable the serial console (it conflicts with the GPS)
sudo raspi-config nonint do_serial_hw 0
sudo raspi-config nonint do_serial_cons 1

# 6. Optional: create an autostart service
read -rp "Install autostart service? [y/N] " ans
if [[ "$ans" =~ ^[Yy]$ ]]; then
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    SERVICE="[Unit]
Description=Spreader GPS Mapper
After=multi-user.target

[Service]
ExecStart=/usr/bin/python3 $SCRIPT_DIR/mapper.py --fullscreen
WorkingDirectory=$SCRIPT_DIR
Restart=on-failure
Environment=SDL_FBDEV=/dev/fb0
Environment=SDL_VIDEODRIVER=fbcon

[Install]
WantedBy=multi-user.target"
    echo "$SERVICE" | sudo tee /etc/systemd/system/spreader-mapper.service
    sudo systemctl daemon-reload
    sudo systemctl enable spreader-mapper.service
    echo "Service installed. Will start on next boot."
fi

echo ""
echo "=== Done. Next steps: ==="
echo "1. Reboot if UART was just enabled: sudo reboot"
echo "2. Generate BOUNDARY.CSV on your PC:"
echo "   python ../app/main.py boundary --address '123 Main St' --card /path/to/sd"
echo "   (or copy it manually to $(dirname "$0")/BOUNDARY.CSV)"
echo "3. Run manually to test:"
echo "   python $(dirname "$0")/mapper.py --fullscreen"
