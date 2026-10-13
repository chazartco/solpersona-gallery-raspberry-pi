"""The agent's local face: media from the card, redirects while downloading, the WebSocket."""

import asyncio
import json

from aiohttp.test_utils import TestClient, TestServer

from solpersona_gallery.config import Config
from solpersona_gallery.server import make_app


class FakeController:
    mode = "player"
    version = "0.0.0"

    def __init__(self, tmp_path):
        self.stored = tmp_path / "film.mp4"
        self.stored.write_bytes(bytes(range(256)) * 40)
        self.messages = []
        self.subs = []

    def media(self, url):
        if url == "https://cdn.example/film.mp4":
            return self.stored, "video/mp4", True
        return None, None, url == "https://cdn.example/new.jpg"

    async def qr(self, item, sample):
        return ("ok", b"\x89PNG...") if item == "a:1" else ("none", None)

    def subscribe(self):
        from solpersona_gallery.controller import Subscriber

        sub = Subscriber()
        sub.put({"type": "state", "mode": "player"})
        self.subs.append(sub)
        return sub

    def unsubscribe(self, sub):
        self.subs.remove(sub)

    def on_player_message(self, msg):
        self.messages.append(msg)


def client_for(tmp_path):
    (tmp_path / "player").mkdir()
    (tmp_path / "player" / "index.html").write_text("<!doctype html><title>player</title>")
    cfg = Config(player_dir=tmp_path / "player", port=8737)
    ctrl = FakeController(tmp_path)
    return ctrl, TestClient(TestServer(make_app(ctrl, cfg)))


def test_media_ranges_redirects_and_unknowns(tmp_path):
    async def main():
        ctrl, client = client_for(tmp_path)
        async with client:
            r = await client.get("/media", params={"u": "https://cdn.example/film.mp4"}, headers={"Range": "bytes=100-199"})
            assert r.status == 206 and len(await r.read()) == 100  # video seeks need ranges
            assert r.headers["Access-Control-Allow-Origin"] == "*"
            r = await client.get("/media", params={"u": "https://cdn.example/new.jpg"}, allow_redirects=False)
            assert r.status == 302 and r.headers["Location"] == "https://cdn.example/new.jpg"
            r = await client.get("/media", params={"u": "https://evil.example/x"}, allow_redirects=False)
            assert r.status == 404  # not an open redirect

    asyncio.run(main())


def test_qr_and_index(tmp_path):
    async def main():
        ctrl, client = client_for(tmp_path)
        async with client:
            assert (await client.get("/qr", params={"item": "a:1"})).status == 200
            assert (await client.get("/qr", params={"item": "a:2"})).status == 404
            assert "player" in await (await client.get("/")).text()
            assert (await client.get("/../etc/passwd")).status == 404

    asyncio.run(main())


def test_websocket_only_from_our_own_page(tmp_path):
    async def main():
        ctrl, client = client_for(tmp_path)
        async with client:
            r = await client.get("/ws", headers={"Origin": "https://some-site.example"})
            assert r.status == 403
            ws = await client.ws_connect("/ws", headers={"Origin": "http://127.0.0.1:8737"})
            first = json.loads((await ws.receive()).data)
            assert first["type"] == "state"
            await ws.send_str(json.dumps({"type": "report", "state": {"itemId": "a:1"}}))
            await ws.send_str("not json")
            await asyncio.sleep(0.05)
            assert ctrl.messages == [{"type": "report", "state": {"itemId": "a:1"}}]
            await ws.close()

    asyncio.run(main())
