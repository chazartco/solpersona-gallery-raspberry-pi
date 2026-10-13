"""The pairing / sync state machine against a mocked device API (CONFORMANCE A).

Each test runs the real Controller against `dev/mock_solpersona.py` served on
localhost, with every wait shortened.
"""

import asyncio
import json
import os
import stat
import time

import aiohttp
from aiohttp.test_utils import TestServer
from mock_solpersona import MockSolPersona

from solpersona_gallery.config import Config
from solpersona_gallery.controller import Controller, Timing
from solpersona_gallery.state_files import StateFiles
from solpersona_gallery.store import MediaStore

MiB = 1024 * 1024
FAST = Timing(
    default_poll_ms=30, backoff_base_ms=20, backoff_max_ms=200, pairing_backoff_ms=20, pairing_backoff_max_ms=100,
    rate_limited_ms=50, register_rate_limited_ms=100, sample_poll_ms=100, sync_soon_ms=5, tick_s=0.02,
)


class FakeDisplay:
    panel = {"connector": "HDMI-A-1", "width": 3840, "height": 2160}
    method = "black"

    def __init__(self):
        self.powered = None
        self.calls = []

    def refresh(self):
        return self.panel

    def logical(self):
        return (3840, 2160)

    def can_hold_originals(self):
        return True

    async def set_power(self, on):
        self.powered = on
        self.calls.append(on)
        return "black"


class Harness:
    def __init__(self, tmp_path, mock=None):
        self.tmp = tmp_path
        self.mock = mock or MockSolPersona(poll_ms=30, interval_ms=20, fast_images=True)

    async def __aenter__(self):
        self.server = TestServer(self.mock.app())
        await self.server.start_server()
        self.base = str(self.server.make_url("")).rstrip("/")
        self.mock.set_public_url(self.base)
        self.session = aiohttp.ClientSession()
        self.ctrl = self.make_controller(self.base)
        return self

    def make_controller(self, base):
        cfg = Config(
            base_url=base, state_dir=self.tmp / "state", cache_dir=self.tmp / "cache", run_dir=self.tmp / "run",
            manage_hardware=False, cache_mb=50, screen_name="Test wall",
        )
        store = MediaStore(cfg.cache_dir, session=lambda: self.session, cap_bytes=lambda: 50 * MiB)
        store.load()
        return Controller(cfg=cfg, files=StateFiles(cfg.state_dir), store=store, display=FakeDisplay(), network=None, session=self.session, timing=FAST)

    async def __aexit__(self, *exc):
        await self.ctrl.stop()
        await self.session.close()
        await self.server.close()

    def link(self):
        for reg in self.mock.registrations.values():
            reg["linked"] = True

    def calls(self, path):
        return [c for c in self.mock.calls if c["path"] == path]

    async def linked(self):
        self.ctrl.start()
        await until(lambda: self.ctrl.registration is not None)
        self.link()
        await until(lambda: self.ctrl.mode == "player" and self.ctrl.show is not None)


async def until(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


def run(coro):
    asyncio.run(coro)


def test_pairing_stores_the_token_first_and_never_shows_it(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            h.ctrl.start()
            await until(lambda: h.ctrl.registration is not None)
            assert h.ctrl.mode == "pairing"
            reg = h.calls("/api/v1/device/register")[0]["body"]
            assert reg["platform"] == "raspberry-pi" and reg["name"] == "Test wall"
            assert reg["capabilities"]["persistentStorage"] is True and reg["capabilities"]["screenWidth"] == 3840
            public = json.dumps(h.ctrl.player_state())
            assert h.ctrl.registration["deviceCode"] not in public  # the device code is a secret too

            h.link()
            await until(lambda: h.ctrl.mode == "player")
            path = tmp_path / "state" / "device.json"
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
            token = json.loads(path.read_text())["token"]
            assert token.startswith("sps_")
            await until(lambda: h.ctrl.show is not None and h.ctrl.status == "PLAYING")
            assert token not in json.dumps(h.ctrl.player_state())
            assert token not in json.dumps({"type": "show", "show": h.ctrl.show})
            assert not (tmp_path / "state" / "pairing.json").exists()

    run(main())


def test_an_expired_code_is_replaced_silently(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            h.ctrl.start()
            await until(lambda: h.ctrl.registration is not None)
            first = h.ctrl.registration["pairingCode"]
            for reg in h.mock.registrations.values():
                reg["expiresAt"] = "2000-01-01T00:00:00Z"  # the server says TOKEN_EXPIRED
            await until(lambda: h.ctrl.registration is not None and h.ctrl.registration["pairingCode"] != first)
            assert len(h.calls("/api/v1/device/register")) >= 2
            assert h.ctrl.mode == "pairing"

    run(main())


def test_register_honours_retry_after(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            h.mock.rate_limit_next = 1
            h.ctrl.start()
            await until(lambda: h.ctrl.registration is not None, timeout=5)
            calls = h.calls("/api/v1/device/register")
            assert len(calls) == 2 and calls[1]["at"] - calls[0]["at"] >= 0.95

    run(main())


def test_new_version_replaces_the_playlist_same_version_only_moves_the_clock(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            await h.linked()
            items_before = h.ctrl.show["items"]
            version = h.ctrl.show["version"]
            # Same version: no playlist in the answer, only a new clock.
            h.mock.step(1)
            await until(lambda: h.ctrl.show["epochMs"] == h.mock.epoch)
            assert h.ctrl.show["items"] is items_before and h.ctrl.show["version"] == version
            last = h.calls("/api/v1/device/sync")[-1]["body"]
            assert last["showId"] == h.ctrl.show["id"] and last["version"] == version
            # New version: settings, items and timeline replaced together.
            h.mock.settings["fit"] = "FRAME"
            h.mock.bump()
            await until(lambda: h.ctrl.show["version"] == version + 1)
            assert h.ctrl.show["settings"]["fit"] == "FRAME"
            assert h.ctrl.show["items"] is not items_before

    run(main())


def test_commands_run_once_and_are_acknowledged(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            await h.linked()
            sub = h.ctrl.subscribe()
            commands = [
                {"seq": 1, "type": "identify", "issuedAt": "x"},
                {"seq": 2, "type": "future-thing", "issuedAt": "x"},
            ]
            h.ctrl.handle_commands(commands)
            h.ctrl.handle_commands(commands)  # delivered again: nothing runs twice
            events = []
            while not sub.queue.empty():
                m = sub.queue.get_nowait()
                if m.get("type") == "event":
                    events.append(m["event"])
            assert events == ["identify"]
            assert h.ctrl.ack_seq == 2
            assert json.loads((tmp_path / "state" / "device.json").read_text())["ackSeq"] == 2
            await until(lambda: h.calls("/api/v1/device/sync")[-1]["body"]["ackSeq"] == 2)

            # Through the server: reload drops the playlist and asks for it whole.
            h.mock.seq = 2
            h.mock.commands.append({"seq": 3, "type": "reload", "issuedAt": "x"})
            await until(lambda: h.ctrl.ack_seq == 3)
            await until(lambda: any(c["body"]["showId"] is None for c in h.calls("/api/v1/device/sync")[-3:]))
            await until(lambda: h.ctrl.show is not None)

    run(main())


def test_removed_from_the_dashboard_goes_back_to_pairing(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            await h.linked()
            registers = len(h.calls("/api/v1/device/register"))
            h.mock.tokens.clear()  # the owner removed the screen
            await until(lambda: h.ctrl.mode == "pairing" and h.ctrl.registration is not None)
            assert not (tmp_path / "state" / "device.json").exists()
            assert not (tmp_path / "state" / "show.json").exists()
            assert len(h.calls("/api/v1/device/register")) == registers + 1

    run(main())


def test_rate_limited_sync_waits_retry_after(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            await h.linked()
            h.mock.rate_limit_next = 1
            n = len(h.calls("/api/v1/device/sync"))
            await until(lambda: len(h.calls("/api/v1/device/sync")) >= n + 2, timeout=5)
            calls = h.calls("/api/v1/device/sync")
            gaps = [b["at"] - a["at"] for a, b in zip(calls[n - 1:], calls[n:])]
            assert max(gaps) >= 0.95
            assert h.ctrl.mode == "player" and h.ctrl.show is not None

    run(main())


def test_offline_keeps_playing_and_reports_plays(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            await h.linked()
            h.ctrl.start = lambda: None
            h.ctrl._spawn("housekeeping", h.ctrl._housekeeping())
            item = h.ctrl.show["items"][0]["id"]
            h.ctrl.on_player_message({"type": "report", "state": {"itemId": item, "visible": True}})
            h.mock.fail_next = [503] * 6
            await until(lambda: not h.ctrl.online)
            assert h.ctrl.show is not None  # never blank the wall because the network blinked
            await asyncio.sleep(0.3)
            h.ctrl.on_player_message({"type": "report", "state": {"itemId": item, "visible": True}})
            await until(lambda: h.ctrl.online, timeout=5)
            await until(lambda: h.mock.plays)
            assert h.mock.plays[0]["itemId"] == item and h.mock.plays[0]["ms"] > 0
            assert all(set(p) == {"itemId", "startedAt", "ms"} for p in h.mock.plays)
            assert len([e for e in h.ctrl.play_log if e["end"] > e["startedAt"]]) == 0

    run(main())


def test_plays_hand_off_never_grows_and_keeps_what_played_meanwhile(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            c = h.ctrl
            c.play_log = [{"itemId": "a", "startedAt": 0, "end": 1000}, {"itemId": "b", "startedAt": 1000, "end": 5000}]
            for _ in range(5):  # failed attempts must not add entries
                taken, out = c._plays_to_send()
            assert taken == 2 and len(c.play_log) == 2 and out[-1] == {"itemId": "b", "startedAt": 1000, "ms": 4000}
            c.play_log[-1]["end"] = 7000  # still on the wall while the request was out
            c._plays_reported(taken)
            assert c.play_log == [{"itemId": "b", "startedAt": 5000, "end": 7000}]
            c.play_log = [{"itemId": str(i), "startedAt": i, "end": i + 1} for i in range(2500)]
            assert len(c._plays_to_send()[1]) == 2000  # SPEC §4.1: at most 2000

    run(main())


def test_nothing_is_recorded_while_dark(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            await h.linked()
            h.ctrl.online = False
            h.ctrl.on_player_message({"type": "report", "state": {"itemId": "a:0001", "visible": False}})
            h.ctrl.record_offline_play()
            h.ctrl.on_player_message({"type": "report", "state": {"itemId": "a:0001", "visible": True}})
            h.ctrl.dark = True
            h.ctrl.record_offline_play()
            assert h.ctrl.play_log == []
            assert h.ctrl.report_state()["visible"] is False

    run(main())


def test_a_video_length_is_reported_once_per_version(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            await h.linked()
            item = h.ctrl.show["items"][1]["id"]
            h.ctrl.on_player_message({"type": "duration", "itemId": item, "ms": 12480.4})
            h.ctrl.on_player_message({"type": "duration", "itemId": item, "ms": 12480.4})
            h.ctrl.on_player_message({"type": "duration", "itemId": "not-in-show", "ms": 5000})
            await until(lambda: h.mock.reported.get(item) == 12480)
            sent = [d for c in h.calls("/api/v1/device/sync") for d in c["body"]["durations"]]
            assert sent == [{"itemId": item, "ms": 12480}]

    run(main())


def test_forget_this_account(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            await h.linked()
            await h.ctrl.forget()
            assert h.calls("/api/v1/device")[-1]["method"] == "DELETE"
            assert h.mock.devices == {}
            assert h.ctrl.mode == "pairing"
            assert not (tmp_path / "state" / "device.json").exists()

    run(main())


def test_sample_gallery_keeps_pairing_and_switches_on_link(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            h.ctrl.start()
            await until(lambda: h.ctrl.registration is not None)
            h.ctrl.on_player_message({"type": "action", "action": "sample"})
            await until(lambda: h.ctrl.mode == "sample" and h.ctrl.show is not None)
            assert h.ctrl.show["id"] == "sample"
            assert h.ctrl.player_state()["registration"]["pairingCode"]  # the code stays visible
            outcome, png = await h.ctrl.qr(h.ctrl.show["items"][0]["id"], sample=True)
            assert outcome == "ok" and png.startswith(b"\x89PNG")
            h.link()  # linked from a phone while the sample plays
            await until(lambda: h.ctrl.mode == "player" and h.ctrl.show is not None and h.ctrl.show["id"] != "sample")
            assert not (tmp_path / "state" / "show.json").read_text().count('"sample"')

    run(main())


def test_stop_sample_returns_to_pairing(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            h.ctrl.start()
            await until(lambda: h.ctrl.registration is not None)
            code = h.ctrl.registration["pairingCode"]
            h.ctrl.on_player_message({"type": "action", "action": "sample"})
            await until(lambda: h.ctrl.mode == "sample")
            h.ctrl.on_player_message({"type": "action", "action": "stop-sample"})
            assert h.ctrl.mode == "pairing" and h.ctrl.show is None
            assert h.ctrl.registration["pairingCode"] == code

    run(main())


def test_reboot_without_network_plays_the_stored_playlist(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            await h.linked()
            show_id = h.ctrl.show["id"]
            await h.ctrl.stop()
            # "Reboot" with SolPersona unreachable.
            h.ctrl = h.make_controller(h.base)
            await h.server.close()
            h.ctrl.cfg.base_url = h.base
            h.ctrl.start()
            assert h.ctrl.mode == "player" and h.ctrl.show and h.ctrl.show["id"] == show_id
            await until(lambda: not h.ctrl.online)
            assert h.ctrl.show["id"] == show_id
            h.server = TestServer(h.mock.app())  # for __aexit__
            await h.server.start_server()

    run(main())


def test_qr_codes_are_fetched_with_the_token_and_kept(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            await h.linked()
            item = h.ctrl.show["items"][0]["id"]
            outcome, png = await h.ctrl.qr(item, sample=False)
            assert outcome == "ok" and png.startswith(b"\x89PNG")
            assert h.calls("/api/v1/device/qr")[-1]["auth"].startswith("Bearer sps_")
            n = len(h.calls("/api/v1/device/qr"))
            await h.ctrl.qr(item, sample=False)
            assert len(h.calls("/api/v1/device/qr")) == n  # cached for the show version
            assert (await h.ctrl.qr("a:not-in-show", sample=False))[0] == "none"

    run(main())


def test_every_qr_code_is_kept_for_offline(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            await h.linked()
            await until(lambda: len({c["path"] + str(c["body"]) for c in h.calls("/api/v1/device/qr")}) >= 1)
            await until(lambda: len(list((tmp_path / "cache" / "qr").glob("*.png"))) == len(h.ctrl.items), timeout=5)

    run(main())


def test_unlicensed_plays_nothing(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            await h.linked()
            h.mock.status = "UNLICENSED"
            await until(lambda: h.ctrl.status == "UNLICENSED")
            assert h.ctrl.show is None and h.ctrl.wants() == []
            h.mock.status = "PLAYING"
            await until(lambda: h.ctrl.status == "PLAYING" and h.ctrl.show is not None)

    run(main())
