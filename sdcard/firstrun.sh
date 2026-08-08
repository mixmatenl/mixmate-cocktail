#!/bin/bash
# MIXMATE Cocktailmachine — Eerste opstart installatie
# Dit script draait automatisch bij de eerste boot van Pi OS Lite.
# Geen SSH, geen toetsenbord nodig.
#
# Vereiste: Pompmodule is aan en hotspot MIXMATE-SETUP is actief.

set -e
exec > /boot/firmware/mixmate-install.log 2>&1
echo "=== MIXMATE Installatie gestart: $(date) ==="

INSTALL_DIR="/home/pi/mixmate-cocktail"
BOOT_SRC="/boot/firmware/mixmate-cocktail"
SSID="MIXMATE-SETUP"
PASSWORD="mixmate123"
SERVICE="mixmate-cocktail"

# ── Stap 1: Systeem updaten en benodigde pakketten installeren ────────────────
echo "[1/6] Systeempakketten installeren..."
apt-get update -y
apt-get install -y git python3-pip python3-full bluetooth bluez

# ── Stap 2: Verbinden met installatie-hotspot (Pompmodule) ───────────────────
echo "[2/6] Verbinden met MIXMATE-SETUP hotspot..."
nmcli radio wifi on
sleep 2
nmcli dev wifi connect "$SSID" password "$PASSWORD" || {
    echo "Hotspot niet gevonden — wachten 30s en opnieuw proberen..."
    sleep 30
    nmcli dev wifi connect "$SSID" password "$PASSWORD" || {
        echo "WAARSCHUWING: Hotspot niet bereikbaar. Doorgaan zonder internet."
    }
}
sleep 5
echo "Netwerk status: $(nmcli -t -f DEVICE,STATE dev | grep wlan)"

# ── Stap 3: Bronbestanden kopiëren van SD-kaart ──────────────────────────────
echo "[3/6] Bestanden kopiëren van SD-kaart..."
mkdir -p "$INSTALL_DIR"
cp -r "$BOOT_SRC/"* "$INSTALL_DIR/"
chown -R pi:pi "$INSTALL_DIR"
echo "Bestanden gekopieerd naar $INSTALL_DIR"

# ── Stap 4: Python-dependencies installeren ───────────────────────────────────
echo "[4/6] Python packages installeren..."
pip3 install --break-system-packages websockets zeroconf

# Pi versie detecteren
PI_VERSION="4"
if grep -q "BCM2712\|Raspberry Pi 5" /proc/cpuinfo 2>/dev/null; then
    PI_VERSION="5"
fi
echo "Raspberry Pi versie gedetecteerd: Pi $PI_VERSION"

if [ "$PI_VERSION" = "5" ]; then
    pip3 install --break-system-packages lgpio
else
    pip3 install --break-system-packages RPi.GPIO hx711
fi
echo "Python packages geïnstalleerd"

# ── Stap 5: Systemd service installeren ──────────────────────────────────────
echo "[5/6] Systemd service installeren..."
cp "$INSTALL_DIR/mixmate-cocktail.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE"
echo "Service geïnstalleerd en ingeschakeld"

# ── Stap 6: WiFi-hotspot verbinding vergeten + opruimen ───────────────────────
echo "[6/6] Opruimen..."
nmcli con delete "$SSID" 2>/dev/null || true

# Verwijder firstrun uit cmdline.txt zodat het niet opnieuw draait
CMDLINE="/boot/firmware/cmdline.txt"
sed -i "s| systemd.run=/boot/firmware/firstrun.sh||g" "$CMDLINE"
sed -i "s| systemd.run_success_action=reboot||g" "$CMDLINE"
sed -i "s| systemd.unit=kernel-command-line.target||g" "$CMDLINE"

echo "=== MIXMATE Installatie geslaagd: $(date) ==="
echo "Herstart in 5 seconden..."
sleep 5
reboot
