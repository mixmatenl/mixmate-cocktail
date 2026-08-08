#!/bin/bash
# MIXMATE Cocktailmachine — Installatiescript
# Uitvoeren via SSH: bash <(curl -fsSL https://raw.githubusercontent.com/mixmatenl/mixmate-cocktail/main/install.sh)

set -e
echo "=== MIXMATE Cocktailmachine installatie ==="

INSTALL_DIR="/home/pi/mixmate-cocktail"
SERVICE="mixmate-cocktail"

# ── Pakketten ─────────────────────────────────────────────────────────────────
echo "[1/4] Pakketten installeren..."
sudo apt-get update -y
sudo apt-get install -y git python3-pip bluetooth bluez

# ── Pi versie detecteren ──────────────────────────────────────────────────────
PI_VERSION="4"
if grep -q "BCM2712\|Raspberry Pi 5" /proc/cpuinfo 2>/dev/null; then
    PI_VERSION="5"
fi
echo "    Raspberry Pi $PI_VERSION gedetecteerd"

# ── Repo klonen of updaten ────────────────────────────────────────────────────
echo "[2/4] Repository ophalen..."
if [ -d "$INSTALL_DIR/.git" ]; then
    git -C "$INSTALL_DIR" pull
else
    git clone https://github.com/mixmatenl/mixmate-cocktail.git "$INSTALL_DIR"
fi

# ── Python dependencies ───────────────────────────────────────────────────────
echo "[3/4] Python packages installeren..."
pip3 install --break-system-packages websockets zeroconf

if [ "$PI_VERSION" = "5" ]; then
    pip3 install --break-system-packages lgpio
else
    pip3 install --break-system-packages RPi.GPIO hx711
fi

# ── Sudoers voor nmcli (hotspot verbinding) ───────────────────────────────────
echo "pi ALL=(ALL) NOPASSWD: /usr/bin/nmcli" | sudo tee /etc/sudoers.d/mixmate-cocktail > /dev/null
sudo chmod 440 /etc/sudoers.d/mixmate-cocktail

# ── Systemd service ───────────────────────────────────────────────────────────
echo "[4/4] Service installeren..."
sudo cp "$INSTALL_DIR/mixmate-cocktail.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE"
sudo systemctl restart "$SERVICE"

echo ""
echo "=== Installatie geslaagd! ==="
echo "Logs bekijken: journalctl -u mixmate-cocktail -f"
