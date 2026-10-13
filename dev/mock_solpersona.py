#!/usr/bin/env python3
"""A stand-in for SolPersona's device API, for tests and for development
without a SolPersona stack.

It implements only what `docs/solpersona/SPEC.md` documents — register, token,
sync, qr, forget, sample, sample/qr — plus a `/mock` page with the controls
the real dashboard has (link the screen, play, pause, next, show this now,
settings, commands, unlicensed, remove the screen, fail the next polls). It is
not SolPersona: when the two disagree, the real one and SPEC.md win.

    python3 dev/mock_solpersona.py --port 8080
    open http://localhost:8080/mock

Needs aiohttp; Pillow makes real pictures (without it, every piece is a tiny
grey PNG).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import secrets
import struct
import time
import zlib
from datetime import datetime, timezone
from typing import Any, Optional

from aiohttp import web

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0 O 1 I
CLIP = __import__("pathlib").Path(__file__).resolve().parent / "fixtures" / "clip-7s.mp4"  # dev/make-clip.sh
DEFAULT_SETTINGS: dict[str, Any] = {
    "slideSeconds": 20, "order": "GALLERY", "transition": "FADE", "transitionMs": 1200,
    "loopShortVideos": True, "maxVideoLoops": 3, "videoSound": False, "fit": "BLUR",
    "washOpacity": 60, "pieceShadow": True, "frameStyle": "GALLERY_WHITE", "marginPercent": 4,
    "imageQuality": "AUTO", "captionStyle": "LABEL", "captionTiming": "ALWAYS",
    "captionCorner": "BOTTOM_LEFT", "showTitle": True, "showArtist": True, "showYear": True,
    "showDescription": False, "qrTarget": "SOLPERSONA", "qrCorner": "BOTTOM_RIGHT",
    "burnInGuard": True, "prefetch": 2, "schedule": None,
}

PIECES = [
    ("Morning over the estuary", "Ada Lindqvist", 2021, (3000, 2000), ((235, 186, 120), (60, 92, 140))),
    ("Untitled (blue field)", "Tomás Ferreira", 2019, (1600, 2400), ((24, 46, 110), (140, 180, 230))),
    ("The long table", "Mira Okafor", 2023, (4000, 2250), ((90, 30, 40), (240, 210, 160))),
    ("Salt", "Jun Watanabe", 2020, (2000, 2000), ((245, 245, 240), (200, 200, 190))),
    ("Night garden", "Ada Lindqvist", 2024, (2400, 1600), ((8, 20, 14), (40, 120, 70))),
]


def iso(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def now_ms() -> float:
    return time.time() * 1000


def tiny_png(rgb=(128, 128, 128)) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"\x00" + bytes(rgb)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def make_picture(size: tuple[int, int], colours, fmt: str, title: str = "") -> bytes:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return tiny_png(colours[0])
    w, h = size
    top, bottom = colours
    im = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(im)
    for y in range(h):
        t = y / max(1, h - 1)
        draw.line([(0, y), (w, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    # A few shapes, so framing and the wash have something to show.
    r = min(w, h) // 5
    draw.ellipse([w * 0.62 - r, h * 0.3 - r, w * 0.62 + r, h * 0.3 + r], fill=tuple(255 - c // 2 for c in top))
    draw.rectangle([w * 0.12, h * 0.62, w * 0.42, h * 0.9], fill=tuple(c // 2 for c in bottom))
    out = io.BytesIO()
    im.save(out, fmt, quality=88)
    return out.getvalue()


def make_qr_png(text: str) -> bytes:
    """Not a real QR code — a recognisable placeholder with the link in its pixels."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return tiny_png((0, 0, 0))
    im = Image.new("RGB", (360, 360), "white")
    draw = ImageDraw.Draw(im)
    seed = zlib.crc32(text.encode())
    for y in range(21):
        for x in range(21):
            corner = (x < 7 and y < 7) or (x > 13 and y < 7) or (x < 7 and y > 13)
            on = ((x in (0, 6) or y in (0, 6) or 2 <= x <= 4 and 2 <= y <= 4) if corner else (seed >> ((x * 21 + y) % 31)) & 1)
            if corner and x > 13:
                on = x in (14, 20) or y in (0, 6) or 16 <= x <= 18 and 2 <= y <= 4
            if corner and y > 13:
                on = x in (0, 6) or y in (14, 20) or 2 <= x <= 4 and 16 <= y <= 18
            if on:
                draw.rectangle([18 + x * 15.4, 18 + y * 15.4, 18 + (x + 1) * 15.4 - 1, 18 + (y + 1) * 15.4 - 1], fill="black")
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


class MockSolPersona:
    def __init__(self, public_url: str = "", poll_ms: int = 3000, interval_ms: int = 3000, fast_images: bool = False) -> None:
        self.public_url = public_url
        self.poll_ms = poll_ms
        self.interval_ms = interval_ms
        self.fast_images = fast_images
        self.registrations: dict[str, dict] = {}  # deviceCode → registration
        self.tokens: dict[str, dict] = {}  # token → device
        self.devices: dict[str, dict] = {}
        self.calls: list[dict] = []
        self.files: dict[str, tuple[bytes, str]] = {}
        self.fail_next: list[int] = []  # statuses to answer the next syncs with
        self.rate_limit_next: Optional[int] = None
        self.status = "PLAYING"
        self.owner = "chaz"
        self.keep_offline = False
        self.commands: list[dict] = []
        self.seq = 0
        self.takeover: Optional[dict] = None
        self.settings = dict(DEFAULT_SETTINGS)
        self.show_id = "show-" + secrets.token_hex(3)
        self.version = 1
        self.reported: dict[str, int] = {}
        self.items = self._make_items()
        self.timeline = self.timeline_for(self.items)
        self.epoch = now_ms()
        self.paused_at: Optional[float] = None
        self.plays: list[dict] = []
        self.last_state: dict = {}

    # Content -------------------------------------------------------------------

    def set_public_url(self, url: str) -> None:
        """Media URLs are absolute; a test server only knows its port once started."""
        self.public_url = url.rstrip("/")
        self.items = self._make_items()
        self.timeline = self.timeline_for(self.items)

    def _make_items(self) -> list[dict]:
        items = self._pictures()
        if CLIP.exists() and not self.fast_images:
            # A film: its slot is a guess until a screen reports its real length (SPEC §6.4).
            self.files["clip-poster.webp"] = (make_picture((1280, 720), ((30, 30, 60), (200, 80, 40)), "WEBP"), "image/webp")
            items.append(
                {
                    "id": "a:0006", "kind": "VIDEO", "title": "Seven seconds", "artist": {"name": "Test Pattern", "handle": None},
                    "year": 2026, "description": None,
                    "media": {
                        "original": f"{self.public_url}/media/clip.mp4",
                        "display": f"{self.public_url}/media/clip-poster.webp",
                        "poster": f"{self.public_url}/media/clip-poster.webp",
                        "fallbacks": [],
                    },
                    "links": {"solpersona": "https://solpersona.com/art/a:0006", "vault": None, "marketplace": None},
                }
            )
        return items

    def timeline_for(self, items: list[dict]) -> list[dict]:
        out = []
        for i in items:
            ms = self.settings["slideSeconds"] * 1000
            if i["kind"] == "VIDEO":
                known = self.reported.get(i["id"])
                if known:
                    plays = min(self.settings["maxVideoLoops"], max(1, -(-ms // known))) if self.settings["loopShortVideos"] else 1
                    out.append({"id": i["id"], "ms": known * plays})
                    continue
                out.append({"id": i["id"], "ms": ms, "estimated": True})
            else:
                out.append({"id": i["id"], "ms": ms})
        return out

    def retime(self) -> None:
        """A new timeline without moving what is on screen (reanchor)."""
        before = self.timeline
        index, offset = self.position()
        current = before[index]["id"]
        self.timeline = self.timeline_for(self.items)
        new_index = next((n for n, s in enumerate(self.timeline) if s["id"] == current), 0)
        offset = min(offset, self.timeline[new_index]["ms"] - 1)
        start = sum(s["ms"] for s in self.timeline[:new_index])
        self.epoch = (self.paused_at if self.paused_at is not None else now_ms()) - start - offset
        self.bump()

    def _pictures(self) -> list[dict]:
        items = []
        for n, (title, artist, year, size, colours) in enumerate(PIECES):
            iid = f"a:{n + 1:04d}"
            items.append(
                {
                    "id": iid, "kind": "IMAGE", "title": title,
                    "artist": {"name": artist, "handle": artist.split()[0].lower()}, "year": year,
                    "description": "A study in light and distance, painted over one winter from the same window.",
                    "media": {
                        "original": self._file(f"{iid}-original.jpg", size, colours, "JPEG"),
                        "display": self._file(f"{iid}-display.webp", self._fit(size, 1000), colours, "WEBP"),
                        "poster": self._file(f"{iid}-poster.webp", self._fit(size, 600), colours, "WEBP"),
                        "fallbacks": [],
                    },
                    "links": {"solpersona": f"https://solpersona.com/art/{iid}", "vault": None, "marketplace": None},
                }
            )
        return items

    @staticmethod
    def _fit(size, longest):
        w, h = size
        s = longest / max(w, h)
        return (max(1, int(w * s)), max(1, int(h * s)))

    def _file(self, name: str, size, colours, fmt: str) -> str:
        if self.fast_images:
            self.files[name] = (tiny_png(colours[0]), "image/png")
        else:
            mime = {"JPEG": "image/jpeg", "WEBP": "image/webp", "PNG": "image/png"}[fmt]
            self.files[name] = (make_picture(size, colours, fmt), mime)
        return f"{self.public_url}/media/{name}"

    def bump(self) -> None:
        self.version += 1

    def position(self, at: Optional[float] = None) -> tuple[int, float]:
        total = sum(s["ms"] for s in self.timeline)
        t = (self.paused_at if self.paused_at is not None else (at or now_ms())) - self.epoch
        into = t % total
        for i, slot in enumerate(self.timeline):
            if into < slot["ms"]:
                return i, into
            into -= slot["ms"]
        return len(self.timeline) - 1, 0

    def step(self, by: int) -> None:
        index, _ = self.position()
        index = (index + by) % len(self.timeline)
        start = sum(s["ms"] for s in self.timeline[:index])
        self.epoch = (self.paused_at if self.paused_at is not None else now_ms()) - start

    def show_body(self, full: bool) -> dict:
        body = {
            "id": self.show_id, "version": self.version, "sourceRef": "collection:mock",
            "epochMs": self.epoch, "pausedAtMs": self.paused_at,
        }
        if full:
            body.update({"settings": self.settings, "items": self.items, "timeline": self.timeline})
        return body

    # Device API ------------------------------------------------------------------

    def _err(self, status: int, code: str, message: str, headers=None) -> web.Response:
        return web.json_response({"error": {"code": code, "message": message}}, status=status, headers=headers)

    def _device_for(self, request: web.Request) -> Optional[dict]:
        auth = request.headers.get("Authorization", "")
        return self.tokens.get(auth[7:]) if auth.startswith("Bearer ") else None

    async def _log(self, request: web.Request) -> Any:
        body = None
        if request.can_read_body:
            try:
                body = await request.json()
            except ValueError:
                body = None
        self.calls.append({"method": request.method, "path": request.path, "body": body, "auth": request.headers.get("Authorization"), "at": time.monotonic()})
        return body

    async def register(self, request: web.Request) -> web.Response:
        body = await self._log(request) or {}
        if self.rate_limit_next is not None:
            wait, self.rate_limit_next = self.rate_limit_next, None
            return self._err(429, "RATE_LIMITED", "Slow down.", {"Retry-After": str(wait)})
        code = "".join(secrets.choice(ALPHABET) for _ in range(4)) + "-" + "".join(secrets.choice(ALPHABET) for _ in range(4))
        device_code = secrets.token_urlsafe(24)
        reg = {
            "deviceId": "dev-" + secrets.token_hex(4), "deviceCode": device_code, "pairingCode": code,
            "pairUrl": f"{self.public_url}/pair?code={code}", "qrDataUri": "data:image/png;base64,",
            "expiresAt": iso(now_ms() + 15 * 60 * 1000), "intervalMs": self.interval_ms,
            "name": body.get("name") or "New screen", "linked": False, "capabilities": body.get("capabilities"),
        }
        import base64

        reg["qrDataUri"] = "data:image/png;base64," + base64.b64encode(make_qr_png(reg["pairUrl"])).decode()
        self.registrations[device_code] = reg
        return web.json_response({k: v for k, v in reg.items() if k not in ("linked", "name", "capabilities")}, status=201)

    async def token(self, request: web.Request) -> web.Response:
        body = await self._log(request) or {}
        reg = self.registrations.get(body.get("deviceCode"))
        if not reg:
            return self._err(401, "SCREEN_NOT_LINKED", "Unknown code.")
        if datetime.fromisoformat(reg["expiresAt"].replace("Z", "+00:00")) < datetime.now(timezone.utc):
            return self._err(401, "TOKEN_EXPIRED", "The code ran out.")
        if not reg["linked"]:
            return web.json_response({"status": "PENDING", "expiresAt": reg["expiresAt"]}, status=202)
        del self.registrations[body["deviceCode"]]
        token = "sps_" + secrets.token_urlsafe(24)
        device = {"id": reg["deviceId"], "name": reg["name"], "owner": self.owner}
        self.tokens[token] = device
        self.devices[device["id"]] = device
        return web.json_response({"status": "LINKED", "accessToken": token, "device": {"id": device["id"], "name": device["name"]}})

    async def sync(self, request: web.Request) -> web.Response:
        body = await self._log(request) or {}
        device = self._device_for(request)
        if not device:
            return self._err(401, "SCREEN_NOT_LINKED", "This screen is not linked.")
        if self.fail_next:
            status = self.fail_next.pop(0)
            return self._err(status, "INTERNAL", "Mock failure.")
        if self.rate_limit_next is not None:
            wait, self.rate_limit_next = self.rate_limit_next, None
            return self._err(429, "RATE_LIMITED", "Slow down.", {"Retry-After": str(wait)})
        self.last_state = body.get("state") or {}
        retime = False
        for d in body.get("durations") or []:
            self.reported[d["itemId"]] = d["ms"]
            retime = retime or any(s["id"] == d["itemId"] and s.get("estimated") for s in self.timeline)
        if retime:
            self.retime()  # the real length arrived: re-time around it, the wall does not move
        self.plays.extend(body.get("plays") or [])
        ack = body.get("ackSeq") or 0
        self.commands = [c for c in self.commands if c["seq"] > ack]
        show = None
        if self.status == "PLAYING":
            full = body.get("showId") != self.show_id or body.get("version") != self.version
            show = self.show_body(full)
        tk = self.takeover
        if tk and tk.get("until") and datetime.fromisoformat(tk["until"].replace("Z", "+00:00")) < datetime.now(timezone.utc):
            self.takeover = tk = None
        return web.json_response(
            {
                "serverTime": now_ms(), "pollMs": self.poll_ms,
                "device": {"id": device["id"], "name": device["name"], "owner": device["owner"], "keepOffline": self.keep_offline},
                "status": self.status, "show": show, "takeover": tk, "commands": self.commands,
            }
        )

    async def qr(self, request: web.Request) -> web.Response:
        await self._log(request)
        if not self._device_for(request):
            return self._err(401, "SCREEN_NOT_LINKED", "Not linked.")
        item = request.query.get("item")
        if self.settings["qrTarget"] == "NONE" or item not in {i["id"] for i in self.items} | ({self.takeover["item"]["id"]} if self.takeover else set()):
            return self._err(404, "NOT_FOUND", "No code for this piece.")
        link = f"https://solpersona.com/art/{item}"
        return web.Response(body=make_qr_png(link), content_type="image/png", headers={"x-qr-link": link})

    async def forget(self, request: web.Request) -> web.Response:
        await self._log(request)
        auth = request.headers.get("Authorization", "")[7:]
        device = self.tokens.pop(auth, None)
        if device:
            self.devices.pop(device["id"], None)
        return web.Response(status=204)

    async def sample(self, request: web.Request) -> web.Response:
        await self._log(request)
        day = int(now_ms() // 86_400_000 * 86_400_000)
        return web.json_response(
            {
                "serverTime": now_ms(), "pollMs": 60000,
                "show": {
                    "id": "sample", "version": 3, "sourceRef": "sample", "epochMs": day, "pausedAtMs": None,
                    "settings": {**DEFAULT_SETTINGS, "slideSeconds": 12, "fit": "FRAME", "frameStyle": "OAK"},
                    "items": list(reversed(self.items)), "timeline": [{"id": i["id"], "ms": 12000} for i in reversed(self.items)],
                },
            }
        )

    async def sample_qr(self, request: web.Request) -> web.Response:
        await self._log(request)
        return web.Response(body=make_qr_png("https://solpersona.com/gallery-app"), content_type="image/png")

    async def media(self, request: web.Request) -> web.StreamResponse:
        if request.match_info["name"] == "clip.mp4" and CLIP.exists():
            return web.FileResponse(CLIP, headers={"Access-Control-Allow-Origin": "*"})  # with Range, for seeking
        hit = self.files.get(request.match_info["name"])
        if not hit:
            return web.Response(status=404)
        return web.Response(body=hit[0], content_type=hit[1], headers={"Access-Control-Allow-Origin": "*"})

    # The dashboard's controls -----------------------------------------------------

    async def control(self, request: web.Request) -> web.Response:
        q = request.query
        action = request.match_info["action"]
        if action == "link":
            for reg in self.registrations.values():
                reg["linked"] = True
                reg["name"] = q.get("name", reg["name"])
        elif action == "pause":
            self.paused_at = self.paused_at or now_ms()
        elif action == "resume":
            if self.paused_at is not None:
                self.epoch += now_ms() - self.paused_at
                self.paused_at = None
        elif action == "next":
            self.step(1)
        elif action == "previous":
            self.step(-1)
        elif action == "goto":
            index = next((n for n, s in enumerate(self.timeline) if s["id"] == q.get("item")), 0)
            self.epoch = (self.paused_at if self.paused_at is not None else now_ms()) - sum(s["ms"] for s in self.timeline[:index])
        elif action == "restart":
            self.epoch = now_ms()
            self.paused_at = None
        elif action == "stop":
            self.status = "IDLE"
        elif action == "play":
            self.status = "PLAYING"
        elif action == "unlicensed":
            self.status = "UNLICENSED"
        elif action == "settings":
            for key, raw in q.items():
                if key in self.settings:
                    try:
                        self.settings[key] = json.loads(raw)
                    except ValueError:
                        self.settings[key] = raw
            if "slideSeconds" in q:
                self.retime()
            else:
                self.bump()
        elif action == "takeover":
            item = next((i for i in self.items if i["id"] == q.get("item")), self.items[0])
            seconds = int(q.get("seconds", "60"))
            self.takeover = {"item": item, "settings": self.settings, "startedAt": iso(now_ms()), "until": iso(now_ms() + seconds * 1000) if seconds else None}
        elif action == "dismiss":
            self.takeover = None
        elif action == "command":
            self.seq += 1
            self.commands.append({"seq": self.seq, "type": q.get("type", "identify"), "issuedAt": iso(now_ms())})
        elif action == "keep":
            self.keep_offline = q.get("on", "1") == "1"
        elif action == "unlink":
            self.tokens.clear()
            self.devices.clear()
        elif action == "fail":
            self.fail_next = [int(q.get("status", "503"))] * int(q.get("count", "3"))
        state = {
            "status": self.status, "version": self.version, "paused": self.paused_at is not None,
            "position": self.position()[0], "devices": list(self.devices.values()),
            "pending": [r["pairingCode"] for r in self.registrations.values()],
            "lastState": self.last_state, "reported": self.reported, "plays": len(self.plays),
        }
        if request.headers.get("Accept", "").startswith("application/json") or action != "page":
            return web.json_response(state)
        return web.Response(text=CONTROL_PAGE, content_type="text/html")

    async def page(self, request: web.Request) -> web.Response:
        return web.Response(text=CONTROL_PAGE, content_type="text/html")

    def app(self) -> web.Application:
        app = web.Application()

        @web.middleware
        async def cors(request: web.Request, handler):
            if request.method == "OPTIONS":
                return web.Response(headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "*"})
            response = await handler(request)
            response.headers.setdefault("Access-Control-Allow-Origin", "*")
            return response

        app.middlewares.append(cors)
        r = app.router
        r.add_post("/api/v1/device/register", self.register)
        r.add_post("/api/v1/device/token", self.token)
        r.add_post("/api/v1/device/sync", self.sync)
        r.add_get("/api/v1/device/qr", self.qr)
        r.add_delete("/api/v1/device", self.forget)
        r.add_get("/api/v1/device/sample", self.sample)
        r.add_get("/api/v1/device/sample/qr", self.sample_qr)
        r.add_get("/media/{name}", self.media)
        r.add_get("/mock", self.page)
        r.add_route("*", "/mock/{action}", self.control)
        return app


CONTROL_PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mock SolPersona</title>
<style>body{font:15px system-ui;margin:2rem;max-width:44rem}button{margin:.2rem;padding:.4rem .8rem}pre{background:#f3f3f3;padding:1rem;overflow:auto}</style>
<h1>Mock SolPersona — dashboard</h1>
<p>Not SolPersona. Controls what the mock's device API answers.</p>
<div id="b"></div><pre id="s"></pre>
<script>
const acts = [['link','Link waiting screen'],['play','Play'],['stop','Stop (idle)'],['unlicensed','Unlicensed'],['pause','Pause'],['resume','Resume'],
['previous','Previous'],['next','Next'],['restart','Restart'],['takeover?item=a:0003&seconds=30','Show piece 3 now (30 s)'],['dismiss','Dismiss takeover'],
['command?type=identify','Identify'],['command?type=reload','Reload'],['command?type=clear-cache','Clear cache'],['keep?on=1','Keep offline on'],['keep?on=0','Keep offline off'],
['settings?fit="FRAME"','Fit FRAME'],['settings?fit="BLUR"','Fit BLUR'],['settings?fit="BLACK"','Fit BLACK'],['settings?fit="FILL"','Fit FILL'],
['settings?frameStyle="WALNUT"','Walnut'],['settings?captionStyle="MINIMAL"','One-line label'],['settings?captionStyle="LABEL"','Museum card'],
['settings?transition="DISSOLVE"','Dissolve'],['settings?schedule={"on":"09:00","off":"09:01"}','Closed now'],['settings?schedule=null','Always open'],
['fail?status=503&count=5','Fail 5 polls'],['unlink','Remove screen']];
const b = document.getElementById('b');
for (const [a, l] of acts) { const x = document.createElement('button'); x.textContent = l; x.onclick = () => go(a); b.appendChild(x); }
async function go(a) { const r = await fetch('/mock/' + a, {method:'POST', headers:{Accept:'application/json'}}); show(await r.json()); }
function show(j) { document.getElementById('s').textContent = JSON.stringify(j, null, 2); }
setInterval(() => go('state'), 3000); go('state');
</script>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--public-url", default=None, help="the address screens use for media (default http://localhost:PORT)")
    args = parser.parse_args()
    mock = MockSolPersona(public_url=args.public_url or f"http://localhost:{args.port}")
    print(f"Mock SolPersona on {args.host}:{args.port} — controls at /mock")
    web.run_app(mock.app(), host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
