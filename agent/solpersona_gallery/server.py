"""The agent's face to the player, on 127.0.0.1 only.

    GET /           the player (static files baked into the image)
    GET /ws         a WebSocket: state and show out, reports and actions in
    GET /media?u=   a file from the card, or a redirect to it while it downloads
    GET /qr?item=   a piece's QR code (the agent fetches it with the token)
    GET /healthz    for the kiosk's loader and the watchdog

Nothing here ever carries the access token.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from aiohttp import web

log = logging.getLogger(__name__)

CORS = {"Access-Control-Allow-Origin": "*"}


def allowed_origins(cfg) -> set[str]:
    return {f"http://127.0.0.1:{cfg.port}", f"http://localhost:{cfg.port}", *cfg.dev_origins}


def make_app(controller, cfg) -> web.Application:
    app = web.Application(client_max_size=256 * 1024)
    player_dir = Path(cfg.player_dir)
    origins = allowed_origins(cfg)

    async def index(request: web.Request) -> web.StreamResponse:
        page = player_dir / "index.html"
        if not page.is_file():
            return web.Response(status=503, text="The player is not installed.")
        return web.FileResponse(page, headers={"Cache-Control": "no-cache"})

    async def healthz(request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "mode": controller.mode, "version": controller.version}, headers=CORS)

    async def ws_handler(request: web.Request) -> web.StreamResponse:
        origin = request.headers.get("Origin")
        if origin and origin not in origins:
            # Only our own page may drive the screen (and press "Forget").
            return web.Response(status=403, text="origin not allowed")
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=256 * 1024)
        await ws.prepare(request)
        sub = controller.subscribe()

        async def pump() -> None:
            while True:
                message = await sub.queue.get()
                # Several states queued: only the newest matters.
                if message.get("type") == "state":
                    while not sub.queue.empty():
                        peek = sub.queue.get_nowait()
                        if peek.get("type") != "state":
                            await ws.send_str(json.dumps(peek, separators=(",", ":")))
                        else:
                            message = peek
                await ws.send_str(json.dumps(message, separators=(",", ":")))

        sender = asyncio.ensure_future(pump())
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        controller.on_player_message(json.loads(msg.data))
                    except ValueError:
                        continue
        finally:
            sender.cancel()
            controller.unsubscribe(sub)
        return ws

    async def media(request: web.Request) -> web.StreamResponse:
        url = request.query.get("u", "")
        path, mime, known = controller.media(url)
        if path is not None:
            headers = {**CORS, "Cache-Control": "no-store"}
            if mime and path.suffix == ".bin":
                headers["Content-Type"] = mime
            return web.FileResponse(path, headers=headers)
        if known and url.startswith(("https://", "http://")):
            # Not on the card yet: stream it from its host while the download catches up.
            raise web.HTTPFound(url, headers={**CORS, "Cache-Control": "no-store"})
        return web.Response(status=404, headers=CORS)

    async def qr(request: web.Request) -> web.Response:
        item = request.query.get("item", "")
        sample = request.query.get("sample") == "1"
        outcome, png = await controller.qr(item, sample)
        if outcome == "ok" and png:
            return web.Response(body=png, content_type="image/png", headers={**CORS, "Cache-Control": "no-store"})
        return web.Response(status=404 if outcome == "none" else 503, headers=CORS)

    async def static_file(request: web.Request) -> web.StreamResponse:
        name = request.match_info["name"]
        path = (player_dir / name).resolve()
        if player_dir.resolve() not in path.parents or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Cache-Control": "no-cache"})

    app.router.add_get("/", index)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/media", media)
    app.router.add_get("/qr", qr)
    if (player_dir / "assets").is_dir():
        app.router.add_static("/assets", player_dir / "assets")
    app.router.add_get("/{name:[A-Za-z0-9._-]+}", static_file)
    return app
