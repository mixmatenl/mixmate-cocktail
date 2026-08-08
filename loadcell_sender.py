#!/usr/bin/env python3
"""
MIXMATE Cocktailmachine — Loadcell Sender
Draait op de Cocktailmachine-Pi (2e Pi).

Leest de HX711 weegschaal uit en stuurt meetwaarden via WebSocket
naar de Pompmodule-Pi. Vindt de Pompmodule automatisch via mDNS.

Vereisten:
    pip install websockets hx711 RPi.GPIO zeroconf
"""
import asyncio
import json
import logging
import os
import socket
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [loadcell] %(levelname)s %(message)s",
)
log = logging.getLogger("loadcell")

# ── Configuratie (overridebaar via omgevingsvariabelen) ────────────────────────
DOUT_PIN    = int(os.getenv("LOADCELL_DOUT",  "5"))
SCK_PIN     = int(os.getenv("LOADCELL_SCK",   "6"))
SCALE       = float(os.getenv("LOADCELL_SCALE", "1.0"))
SEND_HZ     = int(os.getenv("LOADCELL_HZ",    "10"))   # meetfrequentie
POMPMODULE_HOST = os.getenv("POMPMODULE_HOST", "")     # leeg = auto-discovery

# ── Hardware ───────────────────────────────────────────────────────────────────
try:
    from hx711 import HX711
    hx = HX711(DOUT_PIN, SCK_PIN)
    hx.set_reading_format("MSB", "MSB")
    hx.set_reference_unit(SCALE)
    hx.reset()
    hx.tare()
    log.info("HX711 initialisatie geslaagd (DOUT=%d, SCK=%d)", DOUT_PIN, SCK_PIN)
    HAS_HX711 = True
except Exception as e:
    log.warning("HX711 niet beschikbaar (%s) — mock modus actief", e)
    HAS_HX711 = False
    hx = None

_mock_weight = 0.0

def read_weight_grams() -> float:
    if hx:
        try:
            val = hx.get_weight(3)
            return max(0.0, float(val))
        except Exception:
            return 0.0
    # Mock: langzaam stijgend gewicht voor testen
    global _mock_weight
    _mock_weight += 0.5
    return _mock_weight % 500


# ── mDNS auto-discovery ────────────────────────────────────────────────────────
def _discover_pompmodule_mdns(timeout: float = 5.0) -> str | None:
    """Zoek de Pompmodule via mDNS (_mixmate._tcp.local)."""
    try:
        from zeroconf import Zeroconf, ServiceBrowser, ServiceStateChange

        found_host = None

        def on_change(zeroconf, service_type, name, state_change):
            nonlocal found_host
            if state_change == ServiceStateChange.Added:
                info = zeroconf.get_service_info(service_type, name)
                if info and info.addresses:
                    import ipaddress
                    addr = str(ipaddress.ip_address(info.addresses[0]))
                    found_host = addr
                    log.info("Pompmodule gevonden via mDNS: %s", addr)

        zc = Zeroconf()
        browser = ServiceBrowser(zc, "_mixmate._tcp.local.", handlers=[on_change])
        deadline = time.monotonic() + timeout
        while found_host is None and time.monotonic() < deadline:
            time.sleep(0.2)
        zc.close()
        return found_host
    except Exception as e:
        log.debug("mDNS discovery mislukt: %s", e)
        return None


def _discover_pompmodule_hostname() -> str | None:
    """Probeer de vaste hostname mixmate.local te resolven."""
    for hostname in ("mixmate.local", "mixmate-pompmodule.local"):
        try:
            addr = socket.getaddrinfo(hostname, None, socket.AF_INET)[0][4][0]
            log.info("Pompmodule gevonden via hostname %s → %s", hostname, addr)
            return addr
        except Exception:
            pass
    return None


def discover_pompmodule() -> str:
    """
    Geeft het IP-adres van de Pompmodule terug.
    Volgorde: env var → mDNS → hostname → wacht en herhaal.
    """
    if POMPMODULE_HOST:
        log.info("Pompmodule host uit omgevingsvariabele: %s", POMPMODULE_HOST)
        return POMPMODULE_HOST

    log.info("Auto-discovery Pompmodule gestart...")
    while True:
        host = _discover_pompmodule_mdns(timeout=5) or _discover_pompmodule_hostname()
        if host:
            return host
        log.warning("Pompmodule niet gevonden — opnieuw zoeken over 5 seconden...")
        time.sleep(5)


# ── WebSocket verbinding & sturen ──────────────────────────────────────────────
async def send_loop():
    import websockets

    host = await asyncio.get_event_loop().run_in_executor(None, discover_pompmodule)
    ws_url = f"ws://{host}:8000/ws/loadcell"
    interval = 1.0 / SEND_HZ
    backoff = 2

    log.info("Verbinden met Pompmodule: %s", ws_url)

    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=10, ping_timeout=5) as ws:
                backoff = 2
                log.info("Verbonden met Pompmodule %s — sturen @ %dHz", host, SEND_HZ)
                while True:
                    weight_g = await asyncio.get_event_loop().run_in_executor(
                        None, read_weight_grams
                    )
                    await ws.send(json.dumps({"weight_g": round(weight_g, 1)}))
                    await asyncio.sleep(interval)

        except Exception as e:
            log.warning("Verbinding verbroken: %s — herverbinden in %ds", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

            # Herdicover als verbinding lang weg was
            if backoff >= 8:
                log.info("Herdicover Pompmodule...")
                try:
                    host = await asyncio.get_event_loop().run_in_executor(
                        None, discover_pompmodule
                    )
                    ws_url = f"ws://{host}:8000/ws/loadcell"
                except Exception:
                    pass


# ── REST API (lokaal) — voor tare commando's van buitenaf ─────────────────────
async def local_api(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Mini HTTP server op poort 8100 — accepteert POST /tare."""
    try:
        data = await asyncio.wait_for(reader.read(512), timeout=2)
        if b"POST /tare" in data:
            if hx:
                await asyncio.get_event_loop().run_in_executor(None, hx.tare)
            response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
        else:
            response = b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n"
        writer.write(response)
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()


async def main():
    log.info("MIXMATE Loadcell Sender gestart")
    server = await asyncio.start_server(local_api, "0.0.0.0", 8100)
    log.info("Lokale API luistert op poort 8100 (POST /tare)")
    await asyncio.gather(send_loop(), server.serve_forever())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Gestopt")
