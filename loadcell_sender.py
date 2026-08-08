#!/usr/bin/env python3
"""
MIXMATE Cocktailmachine — Loadcell Sender
Draait op de Cocktailmachine-Pi (Pi 4 of Pi 5).

Transportlagen (automatische fallback):
  1. WiFi  → WebSocket naar Pompmodule op ws://<pompmodule>:8000/ws/loadcell
  2. Bluetooth → RFCOMM naar Pompmodule als WiFi wegvalt

Hardware:
  Pi 4 → hx711 library + RPi.GPIO
  Pi 5 → lgpio bit-banging (RPi.GPIO werkt niet op Pi 5)

Vereisten:
  pip install websockets zeroconf
  Pi 4: pip install RPi.GPIO hx711
  Pi 5: pip install lgpio
"""

import asyncio
import json
import logging
import os
import socket
import struct
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [loadcell] %(levelname)s %(message)s",
)
log = logging.getLogger("loadcell")

# ── Configuratie ───────────────────────────────────────────────────────────────
DOUT_PIN         = int(os.getenv("LOADCELL_DOUT",   "5"))
SCK_PIN          = int(os.getenv("LOADCELL_SCK",    "6"))
SCALE            = float(os.getenv("LOADCELL_SCALE", "1.0"))
SEND_HZ          = int(os.getenv("LOADCELL_HZ",     "10"))
POMPMODULE_HOST  = os.getenv("POMPMODULE_HOST", "")   # leeg = auto-discovery
BT_CHANNEL       = int(os.getenv("BT_CHANNEL", "1"))
WIFI_RETRY_S      = 5    # seconden wachten voor WiFi retry
BT_FALLBACK_S     = 15   # seconden zonder WiFi voor Bluetooth fallback
HOTSPOT_SSID      = os.getenv("HOTSPOT_SSID",     "MIXMATE-SETUP")
HOTSPOT_PASSWORD  = os.getenv("HOTSPOT_PASSWORD",  "mixmate123")
HOTSPOT_GATEWAY   = os.getenv("HOTSPOT_GATEWAY",   "10.42.0.1")


# ── Pi versie detectie ────────────────────────────────────────────────────────
def _detect_pi_version() -> int:
    try:
        info = open("/proc/cpuinfo").read()
        if "BCM2712" in info or "Raspberry Pi 5" in info:
            return 5
    except Exception:
        pass
    return 4

PI_VERSION = _detect_pi_version()
log.info("Raspberry Pi versie gedetecteerd: Pi %d", PI_VERSION)


# ── HX711 voor Pi 5 (lgpio bit-banging) ──────────────────────────────────────
class HX711lgpio:
    """Minimale HX711 implementatie via lgpio — werkt op Pi 5."""

    def __init__(self, dout: int, sck: int, gain: int = 128):
        import lgpio
        self._lg = lgpio
        self._h    = lgpio.gpiochip_open(0)
        self._dout = dout
        self._sck  = sck
        self._gain_pulses = {128: 1, 64: 3, 32: 2}[gain]
        self._scale      = 1.0
        self._tare_val   = 0.0

        lgpio.gpio_claim_input(self._h, dout)
        lgpio.gpio_claim_output(self._h, sck, lgpio.SET_PULL_NONE, 0)
        self.tare()

    def _wait_ready(self, timeout: float = 1.0) -> bool:
        deadline = time.monotonic() + timeout
        while self._lg.gpio_read(self._h, self._dout) == 1:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.001)
        return True

    def _read_raw(self) -> int:
        if not self._wait_ready():
            return 0
        val = 0
        for _ in range(24):
            self._lg.gpio_write(self._h, self._sck, 1)
            time.sleep(1e-5)
            bit = self._lg.gpio_read(self._h, self._dout)
            self._lg.gpio_write(self._h, self._sck, 0)
            time.sleep(1e-5)
            val = (val << 1) | bit
        # Extra klokpulsen voor gain instelling
        for _ in range(self._gain_pulses):
            self._lg.gpio_write(self._h, self._sck, 1)
            time.sleep(1e-5)
            self._lg.gpio_write(self._h, self._sck, 0)
            time.sleep(1e-5)
        # Two's complement voor 24-bit signed integer
        if val & 0x800000:
            val -= 0x1000000
        return val

    def tare(self, times: int = 10):
        readings = [self._read_raw() for _ in range(times)]
        self._tare_val = sum(readings) / len(readings)

    def get_weight(self, times: int = 3) -> float:
        readings = [self._read_raw() for _ in range(times)]
        avg = sum(readings) / len(readings)
        return (avg - self._tare_val) / self._scale if self._scale != 0 else 0.0

    def set_reference_unit(self, scale: float):
        self._scale = scale

    def reset(self):
        self._lg.gpio_write(self._h, self._sck, 1)
        time.sleep(0.0001)
        self._lg.gpio_write(self._h, self._sck, 0)


# ── Hardware initialisatie ────────────────────────────────────────────────────
hx   = None
_mock_weight = 0.0

def _init_hardware():
    global hx
    if PI_VERSION >= 5:
        try:
            hx = HX711lgpio(DOUT_PIN, SCK_PIN)
            hx.set_reference_unit(SCALE)
            log.info("HX711 via lgpio (Pi 5) geïnitialiseerd — DOUT=%d SCK=%d", DOUT_PIN, SCK_PIN)
            return
        except Exception as e:
            log.warning("lgpio HX711 mislukt: %s", e)
    # Pi 4: standaard hx711 library
    try:
        from hx711 import HX711
        hx = HX711(DOUT_PIN, SCK_PIN)
        hx.set_reading_format("MSB", "MSB")
        hx.set_reference_unit(SCALE)
        hx.reset()
        hx.tare()
        log.info("HX711 via RPi.GPIO (Pi 4) geïnitialiseerd — DOUT=%d SCK=%d", DOUT_PIN, SCK_PIN)
    except Exception as e:
        log.warning("HX711 niet beschikbaar: %s — mock modus actief", e)

_init_hardware()


def read_weight_grams() -> float:
    if hx:
        try:
            return max(0.0, float(hx.get_weight(3)))
        except Exception:
            return 0.0
    # Mock: langzaam stijgend gewicht voor dev/test
    global _mock_weight
    _mock_weight = (_mock_weight + 0.3) % 500
    return round(_mock_weight, 1)


# ── mDNS / hostname discovery ─────────────────────────────────────────────────
def discover_pompmodule_ip() -> str | None:
    # 1. Omgevingsvariabele
    if POMPMODULE_HOST:
        return POMPMODULE_HOST
    # 2. mDNS via zeroconf
    try:
        from zeroconf import Zeroconf, ServiceBrowser, ServiceStateChange
        import ipaddress, threading
        found = threading.Event()
        addr_ref = [None]

        def on_change(zeroconf, service_type, name, state_change, **kwargs):
            if state_change == ServiceStateChange.Added:
                info = zeroconf.get_service_info(service_type, name)
                if info and info.addresses:
                    addr_ref[0] = str(ipaddress.ip_address(info.addresses[0]))
                    found.set()

        zc = Zeroconf()
        ServiceBrowser(zc, "_mixmate._tcp.local.", handlers=[on_change])
        found.wait(timeout=5)
        zc.close()
        if addr_ref[0]:
            log.info("Pompmodule gevonden via mDNS: %s", addr_ref[0])
            return addr_ref[0]
    except Exception as e:
        log.debug("mDNS mislukt: %s", e)
    # 3. Hostname fallback
    for hostname in ("mixmate.local", "mixmate-pompmodule.local"):
        try:
            ip = socket.getaddrinfo(hostname, None, socket.AF_INET)[0][4][0]
            log.info("Pompmodule gevonden via hostname %s → %s", hostname, ip)
            return ip
        except Exception:
            pass
    return None


# ── Installatie-hotspot verbinding ───────────────────────────────────────────

async def connect_to_hotspot() -> bool:
    """Verbindt de Cocktailmachine met de MIXMATE-SETUP hotspot van de Pompmodule."""
    log.info("Hotspot: verbinden met '%s'…", HOTSPOT_SSID)
    try:
        # Check of al verbonden
        rc_check, out_check = await _run(f'nmcli con show --active | grep "{HOTSPOT_SSID}"')
        if rc_check == 0:
            log.info("Hotspot: al verbonden met %s", HOTSPOT_SSID)
            return True
        rc, out = await _run(
            f'nmcli device wifi connect "{HOTSPOT_SSID}" password "{HOTSPOT_PASSWORD}"'
        )
        if rc == 0:
            log.info("Hotspot: verbonden met %s → gateway %s", HOTSPOT_SSID, HOTSPOT_GATEWAY)
            await asyncio.sleep(2)  # wacht tot IP toegewezen is
            return True
        log.warning("Hotspot verbinden mislukt (rc=%d): %s", rc, out)
    except Exception as e:
        log.warning("Hotspot fout: %s", e)
    return False


async def _run(cmd: str) -> tuple:
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode, out.decode().strip()


# ── Bluetooth discovery & transport ──────────────────────────────────────────
_cached_bt_mac: str | None = None


async def fetch_bt_mac_from_api(host: str) -> str | None:
    """Haal het Bluetooth MAC-adres van de Pompmodule op via de lokale API."""
    try:
        import urllib.request
        url = f"http://{host}:8000/api/system/bluetooth-address"
        with urllib.request.urlopen(url, timeout=3) as r:
            data = json.loads(r.read())
            return data.get("address")
    except Exception:
        return None


async def discover_bt_mac() -> str | None:
    """Zoek het Bluetooth MAC-adres van de Pompmodule via bluetoothctl scan."""
    global _cached_bt_mac
    if _cached_bt_mac:
        return _cached_bt_mac
    log.info("Bluetooth: scannen naar MIXMATE Pompmodule…")
    try:
        # Start scan
        scan_proc = await asyncio.create_subprocess_exec(
            "bluetoothctl", "scan", "on",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.sleep(8)
        scan_proc.terminate()
        # Zoek in device lijst
        proc = await asyncio.create_subprocess_exec(
            "bluetoothctl", "devices",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        for line in out.decode().splitlines():
            upper = line.upper()
            if "MIXMATE" in upper:
                parts = line.split()
                if len(parts) >= 2:
                    mac = parts[1]
                    log.info("Pompmodule Bluetooth gevonden: %s", mac)
                    _cached_bt_mac = mac
                    return mac
    except Exception as e:
        log.debug("Bluetooth scan mislukt: %s", e)
    return None


async def ensure_bt_paired(mac: str) -> bool:
    """Koppel (pair) het apparaat als dat nog niet gedaan is — just-works, geen PIN."""
    try:
        # Controleer of al gepaired
        proc = await asyncio.create_subprocess_exec(
            "bluetoothctl", "info", mac,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        if b"Paired: yes" in out:
            return True
        log.info("Bluetooth: koppelen met %s (just-works)…", mac)
        # Just-works agent + pair
        for cmd in [
            f"bluetoothctl agent NoInputNoOutput",
            f"bluetoothctl default-agent",
            f"bluetoothctl pair {mac}",
            f"bluetoothctl trust {mac}",
        ]:
            p = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await p.wait()
        log.info("Bluetooth: %s gekoppeld", mac)
        return True
    except Exception as e:
        log.warning("Bluetooth koppelen mislukt: %s", e)
        return False


async def bluetooth_send_loop(mac: str):
    """Stuurt gewichtsdata via Bluetooth RFCOMM."""
    interval = 1.0 / SEND_HZ
    # Zorg dat devices gepaired zijn voor RFCOMM
    await ensure_bt_paired(mac)
    log.info("Bluetooth: verbinden met %s kanaal %d", mac, BT_CHANNEL)
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    sock.settimeout(10)
    try:
        sock.connect((mac, BT_CHANNEL))
        sock.settimeout(None)
        log.info("Bluetooth verbonden")
        loop = asyncio.get_event_loop()
        while True:
            weight_g = await loop.run_in_executor(None, read_weight_grams)
            msg = json.dumps({"weight_g": round(weight_g, 1)}).encode() + b"\n"
            await loop.run_in_executor(None, sock.sendall, msg)
            await asyncio.sleep(interval)
    except Exception as e:
        log.warning("Bluetooth verbinding verbroken: %s", e)
    finally:
        try:
            sock.close()
        except Exception:
            pass


# ── WiFi WebSocket transport ──────────────────────────────────────────────────
async def wifi_send_loop(host: str):
    """Stuurt gewichtsdata via WebSocket over WiFi."""
    import websockets
    ws_url  = f"ws://{host}:8000/ws/loadcell"
    interval = 1.0 / SEND_HZ
    backoff  = WIFI_RETRY_S
    log.info("WiFi: verbinden met %s", ws_url)
    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=10, ping_timeout=5) as ws:
                global _cached_bt_mac
                if not _cached_bt_mac:
                    mac = await fetch_bt_mac_from_api(host)
                    if mac:
                        _cached_bt_mac = mac
                        log.info("BT MAC gecached: %s (klaar voor fallback)", mac)
                backoff = WIFI_RETRY_S
                log.info("WiFi verbonden met Pompmodule — %dHz", SEND_HZ)
                while True:
                    weight_g = await asyncio.get_event_loop().run_in_executor(
                        None, read_weight_grams
                    )
                    await ws.send(json.dumps({"weight_g": round(weight_g, 1)}))
                    await asyncio.sleep(interval)
        except Exception as e:
            log.warning("WiFi verbroken: %s — retry in %ds", e, backoff)
            raise   # laat de orchestrator weten dat WiFi weg is


# ── Orchestrator: WiFi primair, Bluetooth fallback ────────────────────────────

_active_transport: str = "none"  # "wifi" | "bluetooth" | "none"


async def _wifi_discovery_loop() -> str:
    """Blijft zoeken naar Pompmodule via mDNS/hostname. Geeft IP terug zodra gevonden."""
    while True:
        host = await asyncio.get_event_loop().run_in_executor(None, discover_pompmodule_ip)
        if host:
            return host
        log.info("Pompmodule niet gevonden via WiFi — opnieuw in 5s")
        await asyncio.sleep(5)


async def transport_loop():
    """
    Verbindingsvolgorde:
      1. Installatie-hotspot      (MIXMATE-SETUP, primair)
      2. Bluetooth RFCOMM         (fallback)
      3. WiFi via mDNS/hostname   (als hotspot én Bluetooth niet werken)
    """
    global _active_transport

    log.info("Transport: zoeken naar Pompmodule…")

    while True:
        # ── 1. Installatie-hotspot ────────────────────────────────────────────
        log.info("Probeer installatie-hotspot '%s'…", HOTSPOT_SSID)
        ok = await connect_to_hotspot()
        if ok:
            _active_transport = "hotspot"
            try:
                await wifi_send_loop(HOTSPOT_GATEWAY)
                continue
            except Exception as e:
                log.warning("Hotspot WebSocket mislukt: %s", e)

        # ── 2. Bluetooth fallback ─────────────────────────────────────────────
        _active_transport = "bluetooth"
        log.info("Bluetooth fallback activeren")
        mac = _cached_bt_mac or await discover_bt_mac()
        if mac:
            try:
                await bluetooth_send_loop(mac)
                continue
            except Exception as e:
                log.warning("Bluetooth ook mislukt: %s", e)
        else:
            log.warning("Geen Bluetooth MAC gevonden")

        # ── 3. WiFi via mDNS ─────────────────────────────────────────────────
        log.info("Probeer WiFi via mDNS…")
        host = await asyncio.get_event_loop().run_in_executor(None, discover_pompmodule_ip)
        if host:
            _active_transport = "wifi"
            try:
                await wifi_send_loop(host)
                continue
            except Exception as e:
                log.warning("WiFi WebSocket mislukt: %s", e)

        _active_transport = "none"
        await asyncio.sleep(WIFI_RETRY_S)
        await asyncio.sleep(WIFI_RETRY_S)
        hotspot_tried = False

        # Probeer WiFi opnieuw
        new_host = await asyncio.get_event_loop().run_in_executor(None, discover_pompmodule_ip)
        if new_host:
            host = new_host
            wifi_fail_since = None
            log.info("WiFi teruggevonden — terug naar WiFi")


# ── Lokale REST API (tare commando's) ─────────────────────────────────────────
async def _handle_local_request(reader, writer):
    try:
        data = await asyncio.wait_for(reader.read(512), timeout=2)
        if b"POST /tare" in data:
            if hx:
                await asyncio.get_event_loop().run_in_executor(None, hx.tare)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        else:
            writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()


async def main():
    log.info("MIXMATE Loadcell Sender gestart (Pi %d)", PI_VERSION)
    server = await asyncio.start_server(_handle_local_request, "0.0.0.0", 8100)
    log.info("Lokale API op poort 8100")
    await asyncio.gather(transport_loop(), server.serve_forever())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Gestopt")
