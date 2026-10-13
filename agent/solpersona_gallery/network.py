"""Getting a screen with no keyboard onto the network.

Ethernet just works, and Wi-Fi set in Raspberry Pi Imager just works — both
are NetworkManager's job. This module handles the third case: no network.

If nothing is reachable about 60 s after start, the Pi opens a setup network
`SolPersona-Gallery-XXXX` (an access point on its own Wi-Fi, by
NetworkManager's "shared" mode). The screen says "Join Wi-Fi
SolPersona-Gallery-XXXX on your phone to connect this screen" with a QR code
for it. Joining it opens a small captive page (every DNS name answers with the
Pi, so phones pop it up by themselves) listing the networks seen, where the
person picks theirs and types the password. The Pi then leaves setup mode and
joins it; if that fails, the setup network comes back with the reason.

A power cut takes the router down too, and routers boot slower than a Pi — so
a screen that already knows a Wi-Fi network does not stay in setup mode: every
few minutes, while nobody is using the setup page, it steps out for half a
minute to look for its known networks.

Only `nmcli` is used, through a polkit rule that lets the `gallery` user
manage NetworkManager. Saved networks live on the data partition (the root
file system is read-only), set by NetworkManager.conf.
"""

from __future__ import annotations

import asyncio
import html
import logging
import shutil
import time
from typing import Callable, Optional

from aiohttp import web

from .display import run

log = logging.getLogger(__name__)

HOTSPOT_CONNECTION = "solpersona-setup"
HOTSPOT_ADDRESS = "10.42.0.1"
NO_NETWORK_GRACE_S = 60
RETRY_KNOWN_EVERY_S = 300
RETRY_KNOWN_FOR_S = 30
PORTAL_BUSY_S = 180


def wifi_qr_text(ssid: str, password: str) -> str:
    """The text a phone camera reads as "join this network"."""

    def esc(value: str) -> str:
        return "".join("\\" + c if c in '\\;,:"' else c for c in value)

    if password:
        return f"WIFI:T:WPA;S:{esc(ssid)};P:{esc(password)};;"
    return f"WIFI:T:nopass;S:{esc(ssid)};;"


def split_terse(line: str) -> list[str]:
    """`nmcli -t` fields: ':'-separated, with '\\:' for a literal colon."""
    out, cur, i = [], "", 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            cur += line[i + 1]
            i += 2
            continue
        if c == ":":
            out.append(cur)
            cur = ""
        else:
            cur += c
        i += 1
    out.append(cur)
    return out


def should_open_hotspot(
    *,
    online: bool,
    has_wifi_device: bool,
    ethernet_carrier: bool,
    offline_for_s: float,
    connected_this_boot: bool,
    known_networks: int,
) -> bool:
    """Open the setup network? Only when nothing else can work.

    A screen that was online earlier this boot is having an outage, not a
    setup problem: it keeps playing from the card and NetworkManager keeps
    trying. A plugged-in cable is NetworkManager's to sort out.
    """
    if online or not has_wifi_device or ethernet_carrier:
        return False
    if offline_for_s < NO_NETWORK_GRACE_S:
        return False
    return not connected_this_boot or known_networks == 0


class Network:
    def __init__(self, ssid_suffix: str, password: str, manage: bool, on_change: Callable[[], None]) -> None:
        self.ssid = f"SolPersona-Gallery-{ssid_suffix}"
        self.password = password
        self.enabled = manage and shutil.which("nmcli") is not None
        self.on_change = on_change
        self.status: dict = {"online": None, "kind": None, "ssid": None, "ip": None}
        self.hotspot = False
        self.error: Optional[str] = None
        self.scan: list[dict] = []
        self.known = 0
        self.wifi_device: Optional[str] = None
        self.ethernet_carrier = False
        self.started = time.monotonic()
        self.last_online = 0.0
        self.connected_this_boot = False
        self.portal_seen = 0.0
        self.hotspot_since = 0.0
        self._portal: Optional[web.AppRunner] = None
        self._busy = False

    def public(self) -> dict:
        out = dict(self.status)
        out["hotspot"] = (
            {"ssid": self.ssid, "secured": bool(self.password), "qr": wifi_qr_text(self.ssid, self.password)}
            if self.hotspot
            else None
        )
        out["error"] = self.error
        return out

    # Reading NetworkManager -----------------------------------------------------

    async def refresh(self) -> None:
        code, out = await run("nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device")
        if code != 0:
            return
        kind = ssid = None
        self.wifi_device = None
        self.ethernet_carrier = False
        connected_dev = None
        for line in out.splitlines():
            fields = split_terse(line)
            if len(fields) < 4:
                continue
            dev, typ, state, conn = fields[:4]
            if typ == "wifi":
                self.wifi_device = self.wifi_device or dev
            if typ == "ethernet" and state not in ("unavailable", "unmanaged"):
                self.ethernet_carrier = True
            if state == "connected" and typ in ("ethernet", "wifi") and conn != HOTSPOT_CONNECTION:
                kind, ssid, connected_dev = typ, (conn if typ == "wifi" else None), dev
        ip = None
        if connected_dev:
            code, out = await run("nmcli", "-t", "-g", "IP4.ADDRESS", "device", "show", connected_dev)
            ip = (out.strip().split("|")[0].split("/")[0].strip() or None) if code == 0 else None
        code, out = await run("nmcli", "-t", "-f", "TYPE", "connection", "show")
        if code == 0:
            self.known = sum(1 for line in out.splitlines() if line.strip() == "802-11-wireless") - (1 if self.hotspot else 0)
        online = kind is not None
        changed = online != self.status.get("online") or ssid != self.status.get("ssid") or ip != self.status.get("ip")
        self.status = {"online": online, "kind": kind, "ssid": ssid, "ip": ip}
        if online:
            self.last_online = time.monotonic()
            self.connected_this_boot = True
        if changed:
            self.on_change()

    async def scan_networks(self) -> None:
        if not self.wifi_device:
            return
        code, out = await run("nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes", timeout=25)
        if code != 0:
            return
        best: dict[str, dict] = {}
        for line in out.splitlines():
            fields = split_terse(line)
            if len(fields) < 3 or not fields[0] or fields[0] == self.ssid:
                continue
            signal = int(fields[1]) if fields[1].isdigit() else 0
            if fields[0] not in best or best[fields[0]]["signal"] < signal:
                best[fields[0]] = {"ssid": fields[0], "signal": signal, "secured": fields[2] not in ("", "--")}
        self.scan = sorted(best.values(), key=lambda n: -n["signal"])[:30]

    # The loop -------------------------------------------------------------------------

    async def run_forever(self) -> None:
        if not self.enabled:
            return
        while True:
            try:
                if not self._busy:
                    await self.refresh()
                    await self._decide()
            except asyncio.CancelledError:
                raise
            except Exception:  # never let the network watcher take the agent down
                log.exception("network watcher")
            await asyncio.sleep(5)

    async def _decide(self) -> None:
        now = time.monotonic()
        online = bool(self.status.get("online"))
        if self.hotspot:
            if online:
                await self.stop_hotspot()
                return
            portal_idle = now - self.portal_seen > PORTAL_BUSY_S
            if self.known > 0 and portal_idle and now - self.hotspot_since > RETRY_KNOWN_EVERY_S:
                log.info("setup network: stepping out to look for known networks")
                await self.stop_hotspot()
                await asyncio.sleep(RETRY_KNOWN_FOR_S)
                await self.refresh()
                if not self.status.get("online"):
                    await self.start_hotspot()
            return
        offline_for = now - max(self.last_online, self.started)
        if should_open_hotspot(
            online=online,
            has_wifi_device=self.wifi_device is not None,
            ethernet_carrier=self.ethernet_carrier,
            offline_for_s=offline_for,
            connected_this_boot=self.connected_this_boot,
            known_networks=self.known,
        ):
            await self.start_hotspot()

    async def start_hotspot(self) -> None:
        if not self.wifi_device:
            return
        await self.scan_networks()  # scanning is poor once the radio is an access point
        await run("nmcli", "connection", "delete", HOTSPOT_CONNECTION)
        args = [
            "nmcli", "connection", "add", "save", "no", "type", "wifi", "ifname", self.wifi_device,
            "con-name", HOTSPOT_CONNECTION, "autoconnect", "no", "ssid", self.ssid,
            "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
            "ipv4.method", "shared", "ipv4.addresses", f"{HOTSPOT_ADDRESS}/24", "ipv6.method", "disabled",
        ]
        if self.password:
            args += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", self.password]
        code, out = await run(*args)
        if code == 0:
            code, out = await run("nmcli", "connection", "up", HOTSPOT_CONNECTION, timeout=30)
        if code != 0:
            log.warning("could not open the setup network: %s", out.strip()[:200])
            return
        self.hotspot = True
        self.hotspot_since = time.monotonic()
        await self._start_portal()
        log.info("setup network %s is open", self.ssid)
        self.on_change()

    async def stop_hotspot(self) -> None:
        await self._stop_portal()
        await run("nmcli", "connection", "down", HOTSPOT_CONNECTION, timeout=20)
        await run("nmcli", "connection", "delete", HOTSPOT_CONNECTION)
        if self.hotspot:
            self.hotspot = False
            self.on_change()

    async def join(self, ssid: str, password: str) -> None:
        """Leave setup mode and join the person's network; come back with the reason if it fails."""
        self._busy = True
        self.error = None
        try:
            await self.stop_hotspot()
            await asyncio.sleep(2)
            args = ["nmcli", "device", "wifi", "connect", ssid, "ifname", self.wifi_device or "wlan0"]
            if password:
                args += ["password", password]
            code, out = await run(*args, timeout=45)
            await self.refresh()
            if code != 0 or not self.status.get("online"):
                log.info("could not join %s: %s", ssid, out.strip()[:200])
                self.error = f"Couldn't join {ssid}. Check the name and password, then try again."
                # Forget the failed attempt, so a wrong password is not retried forever.
                await run("nmcli", "connection", "delete", "id", ssid)
                await self.start_hotspot()
            else:
                self.error = None
                log.info("joined %s", ssid)
            self.on_change()
        finally:
            self._busy = False

    # The captive page --------------------------------------------------------------------

    async def _start_portal(self) -> None:
        if self._portal:
            return
        app = web.Application(client_max_size=16 * 1024)
        app.router.add_post("/connect", self._portal_connect)
        app.router.add_route("*", "/{tail:.*}", self._portal_page)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, HOTSPOT_ADDRESS, 80).start()
        except OSError as error:
            log.warning("setup page could not listen: %s", error)
            await runner.cleanup()
            return
        self._portal = runner

    async def _stop_portal(self) -> None:
        if self._portal:
            runner, self._portal = self._portal, None
            await runner.cleanup()

    async def _portal_page(self, request: web.Request) -> web.Response:
        self.portal_seen = time.monotonic()
        return web.Response(text=portal_html(self.ssid, self.scan, self.error), content_type="text/html", headers={"Cache-Control": "no-store"})

    async def _portal_connect(self, request: web.Request) -> web.Response:
        self.portal_seen = time.monotonic()
        form = await request.post()
        # A typed name wins over a ticked one: it is the more deliberate choice.
        ssid = str(form.get("other") or "").strip() or str(form.get("ssid") or "").strip()
        password = str(form.get("password") or "")
        if not ssid or len(ssid.encode("utf-8")) > 32 or (password and not 8 <= len(password) <= 63):
            self.error = "That name or password does not look right. Wi-Fi passwords are 8 to 63 characters."
            return web.Response(text=portal_html(self.ssid, self.scan, self.error), content_type="text/html")
        asyncio.get_running_loop().call_later(1.5, lambda: asyncio.ensure_future(self.join(ssid, password)))
        return web.Response(text=portal_done_html(ssid, self.ssid), content_type="text/html")


_STYLE = """
body{margin:0;font:17px/1.5 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;background:#0b0b0c;color:#ece9e3}
main{max-width:30rem;margin:0 auto;padding:1.5rem 1.25rem 3rem}
h1{font:400 1.6rem/1.2 Georgia,serif;margin:.5rem 0 1rem}
.k{font-size:.8rem;letter-spacing:.16em;text-transform:uppercase;color:#2b7ff5}
label.n{display:flex;justify-content:space-between;gap:1rem;padding:.8rem 1rem;border:1px solid #2a2a2e;border-radius:.6rem;margin:.5rem 0;cursor:pointer}
label.n:has(input:checked){border-color:#2b7ff5;background:#11203a}
input[type=text],input[type=password]{width:100%;box-sizing:border-box;font:inherit;padding:.75rem;border-radius:.6rem;border:1px solid #3a3a3e;background:#151517;color:inherit}
button{font:inherit;font-weight:600;width:100%;padding:.85rem;border:0;border-radius:.6rem;background:#2b7ff5;color:#fff;margin-top:1rem}
.err{background:#3a1d12;color:#ffc9b0;padding:.75rem 1rem;border-radius:.6rem}
.dim{color:#9c9a95;font-size:.9rem}
"""


def portal_html(hotspot: str, networks: list[dict], error: Optional[str]) -> str:
    rows = "".join(
        f'<label class="n"><span><input type="radio" name="ssid" value="{html.escape(n["ssid"], quote=True)}"> '
        f'{html.escape(n["ssid"])}</span><span class="dim">{"🔒 " if n["secured"] else ""}{n["signal"]}%</span></label>'
        for n in networks
    )
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Connect this screen</title><style>{_STYLE}</style></head>
<body><main><div class="k">SolPersona Gallery</div><h1>Connect this screen to Wi-Fi</h1>{err}
<form method="post" action="/connect">
<p class="dim">Choose the network this screen should use.</p>{rows or '<p class="dim">No networks were found. Type the name below.</p>'}
<p><input type="text" name="other" placeholder="Or type a network name" autocomplete="off" autocapitalize="off" spellcheck="false"></p>
<p><input type="password" name="password" placeholder="Password" autocomplete="off"></p>
<button type="submit">Connect</button></form>
<p class="dim">You are connected to {html.escape(hotspot)}, the screen's own setup network.</p></main></body></html>"""


def portal_done_html(ssid: str, hotspot: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Connecting</title><style>{_STYLE}</style></head>
<body><main><div class="k">SolPersona Gallery</div><h1>Connecting to {html.escape(ssid)}…</h1>
<p>The setup network will close now, and your phone will go back to its usual Wi-Fi.</p>
<p>Look at the screen: in a moment it shows a code to link it to your SolPersona account.
If it shows {html.escape(hotspot)} again, the password was not accepted — join it and try once more.</p></main></body></html>"""
