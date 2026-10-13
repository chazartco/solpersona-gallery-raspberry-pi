"""The screen's state machine: pairing → (sample) → player, the poll and its commands.

This is sections 4, 8 and 8b of the reference simulator, moved out of the
browser so the access token never enters it (SPEC §2, §11 "Secrets"). The
player in Chromium is told what to draw over a local WebSocket and reports back
what is on the wall; everything that touches SolPersona happens here.

    pairing ──(linked)──▶ player ──(401 / forget)──▶ pairing
       │  ▲
   (OK)│  │(Back)          the pairing loop keeps running under the sample,
       ▼  │                 so linking from a phone switches straight over
     sample
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import aiohttp

from . import PLATFORM, __version__
from .api import DeviceApi, NetworkError
from .clock import ServerClock
from .rules import choose_candidates, local_minutes, normalise_settings, position_at, schedule_open, video_still
from .state_files import StateFiles
from .store import MediaStore, Want, free_room
from .util import atomic_write_bytes, atomic_write_json, mono_ms, remove, url_key, wall_ms

log = logging.getLogger(__name__)

MiB = 1024 * 1024
UNLINKED_CODES = ("SCREEN_NOT_LINKED", "UNAUTHENTICATED")


@dataclass
class Timing:
    """Every wait the protocol prescribes, in one place (and short in tests)."""

    default_poll_ms: float = 3000
    backoff_base_ms: float = 3000  # sync: 3 s → 6 → 12 → … (SPEC §4.3)
    backoff_max_ms: float = 60000
    pairing_backoff_ms: float = 2000  # pairing: up to 30 s (SPEC §3.3)
    pairing_backoff_max_ms: float = 30000
    rate_limited_ms: float = 15000  # a 429 without Retry-After
    register_rate_limited_ms: float = 60000
    sample_poll_ms: float = 60000
    sync_soon_ms: float = 50
    tick_s: float = 1.0
    player_stale_ms: float = 5000


def parse_time_ms(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000.0
        except ValueError:
            return None
    return None


def host_of(url: str) -> str:
    try:
        return urlsplit(url).netloc or url
    except ValueError:
        return url


UNSENT = "\x00unsent"


class Subscriber:
    """One connected player. Messages queue here; the server sends them."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self.show_sig: Optional[str] = UNSENT
        self.stored_version = -1

    def put(self, message: dict) -> None:
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            pass  # a stuck player gets the next full state anyway


class Controller:
    def __init__(
        self,
        *,
        cfg,
        files: StateFiles,
        store: MediaStore,
        display,
        network,
        session: aiohttp.ClientSession,
        timing: Timing = Timing(),
        version: str = __version__,
    ) -> None:
        self.cfg = cfg
        self.files = files
        self.store = store
        self.display = display
        self.network = network
        self.session = session
        self.api = DeviceApi(session, cfg.base_url)
        self.clock = ServerClock()
        self.timing = timing
        self.version = version

        self.mode = "boot"  # boot | pairing | sample | player
        self.device: Optional[dict] = None  # { baseUrl, deviceId, token, ackSeq } — the token never leaves this process
        self.registration: Optional[dict] = None
        self.pair_error: Optional[str] = None
        self.status: Optional[str] = None  # IDLE | PLAYING | UNLICENSED; None until the first answer
        self.device_info: Optional[dict] = None
        self.show: Optional[dict] = None
        self.items: dict[str, dict] = {}
        self.takeover: Optional[dict] = None
        self.keep_offline = False
        self.online = True
        self.failures = 0
        self.poll_ms = timing.default_poll_ms
        self.last_sync_at: Optional[float] = None
        self.last_sync_ok_wall: Optional[float] = None
        self.next_sync_at: Optional[float] = None
        self.force_full = False
        self.last_error: Optional[str] = None
        self.ack_seq = 0
        self.pending_durations: dict[str, int] = {}
        self.reported_durations: set[str] = set()
        self.play_log: list[dict] = files.load_plays()
        self.plays_dirty = False
        self.handled_commands: deque = deque(maxlen=10)
        self.player_report: dict = {}
        self.player_seen = -1e18
        self.in_use: list[str] = []
        self.dark = False
        self.debug_watchers = 0
        self.subscribers: set[Subscriber] = set()
        self.stored_version = 0
        self.qr_memory: dict[str, Optional[bytes]] = {}
        self.qr_dir = cfg.cache_dir / "qr"

        self._tasks: dict[str, asyncio.Task] = {}
        self._sync_wake = asyncio.Event()
        self._sync_soon = False
        self._syncing = False
        self._sent_caps: Optional[dict] = None
        self._caps: Optional[dict] = None
        self._broadcast_pending = False
        self._known_urls: set[str] = set()
        self._show_sig_pruned: Optional[str] = None
        self._last_alive_touch = 0.0
        self._saved_offset: Optional[float] = None
        self._qr_task_busy = False
        self._qr_retry_at = 0.0  # after an error, prefetching waits (and honours Retry-After)
        self._sent_last_end = 0.0
        self._power_task: Optional[asyncio.Task] = None
        self.on_tick: Optional[Callable[[], None]] = None  # the service pets systemd's watchdog here
        self.store.on_change = self._stored_changed

    # ------------------------------------------------------------------------
    # Life cycle
    # ------------------------------------------------------------------------

    def start(self) -> None:
        self.device = self.files.load_device(self.cfg.base_url)
        if self.device:
            self.start_player()
        else:
            self.start_pairing()
        self._spawn("housekeeping", self._housekeeping())

    async def stop(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        self._save_plays(force=True)
        await self.store.close()

    def _spawn(self, name: str, coro) -> None:
        self._cancel(name)
        task = asyncio.ensure_future(coro)
        self._tasks[name] = task

        def done(t: asyncio.Task) -> None:
            if self._tasks.get(name) is t:
                del self._tasks[name]
            if not t.cancelled() and t.exception() is not None:
                log.error("%s stopped", name, exc_info=t.exception())

        task.add_done_callback(done)

    def _cancel(self, name: str) -> None:
        task = self._tasks.get(name)
        if task and task is not asyncio.current_task():
            task.cancel()
            del self._tasks[name]
        elif task:
            del self._tasks[name]  # the caller is that task; it returns right after

    # ------------------------------------------------------------------------
    # Pairing (SPEC §3)
    # ------------------------------------------------------------------------

    def start_pairing(self) -> None:
        self._cancel("sync")
        self._cancel("sample")
        self.mode = "pairing"
        self.show = None
        self.items = {}
        self.takeover = None
        self.status = None
        self.online = True
        self.failures = 0
        self._known_urls = set()
        self._spawn("pairing", self._pairing_loop())
        self.changed()

    async def _pairing_loop(self) -> None:
        # Anything unexpected (a disk error, a strange answer) restarts the loop
        # rather than leaving a spent code on the wall for ever.
        while True:
            try:
                await self._pairing_run()
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("pairing loop failed; starting it again")
                await asyncio.sleep(5)

    async def _pairing_run(self) -> None:
        saved = self.files.load_pairing()
        expires = parse_time_ms(saved.get("expiresAt")) if saved else None
        if saved and saved.get("baseUrl") == self.cfg.base_url and expires and expires - wall_ms() > 20000:
            self.registration = saved  # a restart keeps showing the same code
        else:
            self.registration = None
        attempt = 0
        while True:
            if self.registration is None:
                self.changed()
                self.registration = await self._register()
                self.files.save_pairing(self.registration)
                self.pair_error = None
                self.changed()
            reg = self.registration
            await asyncio.sleep(max(500.0, float(reg.get("intervalMs") or 3000)) / 1000.0)
            expires = parse_time_ms(reg.get("expiresAt"))
            if expires is not None and expires <= wall_ms():
                self.registration = None  # ran out: a fresh code, silently
                continue
            try:
                r = await self.api.token(str(reg.get("deviceCode") or ""))
            except NetworkError:
                attempt += 1
                self.pair_error = f"Cannot reach {host_of(self.cfg.base_url)}. Retrying…"
                self.changed()
                await asyncio.sleep(min(self.timing.pairing_backoff_max_ms, self.timing.pairing_backoff_ms * attempt) / 1000.0)
                continue
            attempt = 0
            if r.status == 202:
                if self.pair_error:
                    self.pair_error = None
                    self.changed()
                continue
            if r.status == 200 and isinstance(r.data, dict) and r.data.get("accessToken"):
                info = r.data.get("device") if isinstance(r.data.get("device"), dict) else {}
                device = {
                    "baseUrl": self.cfg.base_url,
                    "deviceId": info.get("id") or reg.get("deviceId"),
                    "token": r.data["accessToken"],
                    "ackSeq": 0,
                }
                # Handed over exactly once: stored durably before anything else (SPEC §3.3).
                self.files.save_device(device)
                self.files.save_pairing(None)
                self.device = device
                self.device_info = {"id": device["deviceId"], "name": info.get("name") or "", "owner": None, "keepOffline": False}
                log.info("linked as screen %s", device["deviceId"])
                self.start_player()
                return
            if r.status == 401:  # TOKEN_EXPIRED, or a code that is unknown or spent: a new one
                self.registration = None
                self.files.save_pairing(None)
                continue
            if r.status == 429:
                await asyncio.sleep((r.retry_after_ms or 10000) / 1000.0)
                continue
            self.pair_error = r.message or f"Unexpected answer ({r.status})."
            self.changed()

    async def _register(self) -> dict:
        attempt = 0
        while True:
            body: dict = {"platform": PLATFORM, "appVersion": self.version, "capabilities": self.capabilities()}
            if self.cfg.screen_name:
                body["name"] = self.cfg.screen_name[:60]
            try:
                r = await self.api.register(body)
            except NetworkError:
                attempt += 1
                self.pair_error = f"Cannot reach {host_of(self.cfg.base_url)}. Retrying…"
                self.changed()
                await asyncio.sleep(min(self.timing.pairing_backoff_max_ms, self.timing.pairing_backoff_ms * attempt) / 1000.0)
                continue
            if r.status == 201 and isinstance(r.data, dict) and r.data.get("deviceCode"):
                reg = dict(r.data)
                reg["baseUrl"] = self.cfg.base_url
                return reg
            attempt += 1
            if r.status == 429:
                self.pair_error = "Too many screens asked for a code from here. Trying again shortly."
                wait = r.retry_after_ms or self.timing.register_rate_limited_ms
            else:
                self.pair_error = r.message or f"SolPersona did not answer as expected ({r.status})."
                wait = min(self.timing.pairing_backoff_max_ms, self.timing.pairing_backoff_ms * attempt)
            self.changed()
            await asyncio.sleep(wait / 1000.0)

    def public_registration(self) -> Optional[dict]:
        reg = self.registration
        if not reg:
            return None
        # Everything the screen shows; never the deviceCode.
        return {
            "deviceId": reg.get("deviceId"),
            "pairingCode": reg.get("pairingCode"),
            "pairUrl": reg.get("pairUrl"),
            "qrDataUri": reg.get("qrDataUri"),
            "expiresAt": parse_time_ms(reg.get("expiresAt")),
        }

    # ------------------------------------------------------------------------
    # Try a sample gallery (SPEC §11b)
    # ------------------------------------------------------------------------

    def start_sample(self) -> None:
        if self.mode != "pairing":
            return
        self.mode = "sample"
        self.status = "PLAYING"
        self.show = None
        self.items = {}
        self.takeover = None
        self.online = True
        self.failures = 0
        self._spawn("sample", self._sample_loop())
        self.changed()

    def stop_sample(self) -> None:
        if self.mode != "sample":
            return
        self._cancel("sample")
        self.mode = "pairing"
        self.show = None
        self.items = {}
        self.status = None
        self.online = True
        self._known_urls = set()
        self.qr_memory.clear()
        self.changed()

    async def _sample_loop(self) -> None:
        failures = 0
        while self.mode == "sample":
            try:
                r = await self.api.sample()
            except NetworkError:
                r = None
            if r is not None and r.ok and isinstance(r.data, dict) and isinstance(r.data.get("show"), dict):
                self.clock.add(r.data.get("serverTime"), r.sent_at, r.received_at)
                self.online = True
                failures = 0
                self.last_sync_at = mono_ms()
                self._apply_show(r.data["show"], persist=False)
                every = float(r.data.get("pollMs") or 0) or self.timing.sample_poll_ms
            elif r is not None and self.show is None and r.status != 429 and r.status < 500:
                self.stop_sample()
                self.pair_error = "The sample gallery is not available right now."
                self.changed()
                return
            else:
                self.online = False
                failures += 1
                every = self.timing.rate_limited_ms if r is not None and r.status == 429 else min(
                    self.timing.backoff_max_ms, self.timing.backoff_base_ms * 2 ** (failures - 1)
                )
            self.next_sync_at = mono_ms() + every
            self.changed()
            await asyncio.sleep(every / 1000.0)

    # ------------------------------------------------------------------------
    # Playing: the poll (SPEC §4)
    # ------------------------------------------------------------------------

    def start_player(self) -> None:
        self._cancel("pairing")
        self._cancel("sample")
        self.registration = None
        self.mode = "player"
        self.status = None
        self.show = None
        self.items = {}
        self.ack_seq = int(self.device.get("ackSeq") or 0) if self.device else 0
        self._load_snapshot()  # before any network: a reboot offline plays the last playlist
        self._sent_caps = None
        self._spawn("sync", self._sync_loop())
        self.changed()

    def sync_soon(self) -> None:
        if self._syncing:
            self._sync_soon = True
        else:
            self._sync_wake.set()

    async def _sync_loop(self) -> None:
        while self.mode == "player" and self.device:
            try:
                delay = await self.sync_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A disk error or an answer we cannot read must not stop the heartbeat for good.
                log.exception("sync failed")
                delay = self._sync_failed("sync failed", network=False)
            if delay is None:
                return
            self.next_sync_at = mono_ms() + delay
            self._sync_wake.clear()
            try:
                await asyncio.wait_for(self._sync_wake.wait(), timeout=delay / 1000.0)
            except asyncio.TimeoutError:
                pass

    async def sync_once(self) -> Optional[float]:
        """One poll. Returns the wait before the next, or None when the screen was unlinked."""
        if not self.device:
            return None
        self._syncing = True
        try:
            durations = [{"itemId": k, "ms": v} for k, v in list(self.pending_durations.items())[:50]]
            full = self.force_full or self.show is None
            plays_taken, sent_plays = self._plays_to_send()
            caps = self.capabilities()
            body: dict = {
                "showId": None if full else self.show["id"],
                "version": None if full else self.show["version"],
                "ackSeq": self.ack_seq,
                "durations": durations,
                "state": self.report_state(),
                "appVersion": self.version,
            }
            if caps != self._sent_caps:
                body["capabilities"] = caps
            if sent_plays:
                body["plays"] = sent_plays
            try:
                r = await self.api.sync(self.device["token"], body)
            except NetworkError:
                return self._sync_failed("offline", network=True)
            self.last_sync_at = mono_ms()
            if r.status == 401 and r.code in UNLINKED_CODES:
                log.info("this screen was removed from its account (%s)", r.code)
                self.unlink_locally()
                return None
            if r.status == 429:
                self.last_error = "RATE_LIMITED"
                return r.retry_after_ms if r.retry_after_ms is not None else self.timing.rate_limited_ms
            if not r.ok or not isinstance(r.data, dict):
                if 400 <= r.status < 500 and plays_taken:
                    # The server refused the request (e.g. 422): if the offline log was the
                    # reason, resending it for ever would stop this screen syncing. Drop it.
                    log.warning("sync refused (%s) with %d plays; dropping the offline log", r.status, len(sent_plays))
                    self._plays_reported(plays_taken)
                return self._sync_failed(r.code or f"HTTP {r.status}", network=r.status >= 500)
            data = r.data
            self.clock.add(data.get("serverTime"), r.sent_at, r.received_at)
            for d in durations:
                if self.pending_durations.get(d["itemId"]) == d["ms"]:
                    del self.pending_durations[d["itemId"]]
            if plays_taken:
                self._plays_reported(plays_taken)
            self._sent_caps = caps
            self.online = True
            self.failures = 0
            if self.last_error and not self.last_error.startswith("could not load"):
                self.last_error = None
            self.last_sync_ok_wall = wall_ms()
            self.force_full = False
            self.poll_ms = float(data.get("pollMs") or 0) or self.timing.default_poll_ms
            self.status = data.get("status")
            if isinstance(data.get("device"), dict):
                self.device_info = data["device"]
            self._apply_show(None if self.status == "UNLICENSED" else data.get("show"))
            # Only a real answer switches keeping on or off: a screen that boots with
            # no network keeps what it holds until the server says otherwise.
            self.keep_offline = bool(isinstance(data.get("device"), dict) and data["device"].get("keepOffline") is True)
            self._apply_takeover(data.get("takeover"))
            self.handle_commands(data.get("commands") or [])
            self._save_snapshot(only_if_offset_moved=True)
            self.changed()
            if self._sync_soon:
                self._sync_soon = False
                return self.timing.sync_soon_ms
            return self.poll_ms
        finally:
            self._syncing = False

    def _sync_failed(self, reason: str, network: bool) -> float:
        # Keep playing by the last clock and try again with backoff (SPEC §4.3).
        self.online = False
        self.failures += 1
        self.last_error = reason
        self.changed()
        return min(self.timing.backoff_max_ms, self.timing.backoff_base_ms * 2 ** (self.failures - 1))

    def report_state(self) -> dict:
        """`state` for the owner's dashboard (≤ 4 KB)."""
        r = self.player_report if self.player_connected() else {}
        stored, total = self.store.counts()
        error = r.get("error") or self.last_error or self.store.error
        if not self.player_connected() and self.mode == "player":
            error = error or "the display app is restarting"
        return {
            "itemId": r.get("itemId"),
            "index": r.get("index"),
            "paused": bool(r.get("paused")),
            "cached": int(r.get("cached") or 0),
            "bufferedAhead": int(r.get("bufferedAhead") or 0),
            "error": str(error)[:200] if error else None,
            # Honest (SPEC §4.1): dark outside opening hours, or with nothing drawing.
            "visible": bool(self.player_connected() and r.get("visible") is not False and not self.dark),
            "stored": stored,
            "total": total,
        }

    def player_connected(self) -> bool:
        return mono_ms() - self.player_seen < self.timing.player_stale_ms

    # The playlist --------------------------------------------------------------------

    def _apply_show(self, incoming: Any, persist: bool = True) -> None:
        """The `show` part of an answer. The playlist only comes when ours differs."""
        prev = self.show
        if not isinstance(incoming, dict):
            if prev is not None:
                self.show = None
                self.items = {}
                self._refresh_known()
                if persist:
                    self._save_snapshot()
            return
        if isinstance(incoming.get("items"), list) and isinstance(incoming.get("timeline"), list) and incoming.get("settings") is not None:
            # Atomically: settings, items, timeline together (SPEC §4.2).
            items = [i for i in incoming["items"] if isinstance(i, dict) and isinstance(i.get("id"), str)]
            timeline = [
                {"id": s["id"], "ms": float(s["ms"]), **({"estimated": True} if s.get("estimated") else {})}
                for s in incoming["timeline"]
                if isinstance(s, dict) and isinstance(s.get("id"), str) and isinstance(s.get("ms"), (int, float)) and s["ms"] >= 0
            ]
            self.show = {
                "id": incoming.get("id"),
                "version": incoming.get("version"),
                "sourceRef": incoming.get("sourceRef"),
                "epochMs": float(incoming.get("epochMs") or 0),
                "pausedAtMs": incoming.get("pausedAtMs"),
                "settings": normalise_settings(incoming.get("settings")),
                "items": items,
                "timeline": timeline,
            }
            self.items = {i["id"]: i for i in items}
            self._refresh_known()
            if persist:
                self._save_snapshot()
            return
        if prev is not None and prev.get("id") == incoming.get("id"):
            moved = prev.get("epochMs") != incoming.get("epochMs") or prev.get("pausedAtMs") != incoming.get("pausedAtMs")
            prev["version"] = incoming.get("version", prev.get("version"))
            prev["sourceRef"] = incoming.get("sourceRef", prev.get("sourceRef"))
            prev["epochMs"] = float(incoming.get("epochMs") or 0)
            prev["pausedAtMs"] = incoming.get("pausedAtMs")
            if moved and persist:
                self._save_snapshot()
            return
        # A show we do not hold, and no playlist with it: ask again for the whole thing.
        self.force_full = True
        self.sync_soon()

    def _apply_takeover(self, raw: Any) -> None:
        if not isinstance(raw, dict) or not isinstance(raw.get("item"), dict):
            self.takeover = None
        else:
            self.takeover = {
                "item": raw["item"],
                "settings": normalise_settings(raw.get("settings")),
                "startedAt": raw.get("startedAt"),
                "until": raw.get("until"),
            }
        self._refresh_known()

    def active_takeover(self, now: Optional[float] = None) -> Optional[dict]:
        tk = self.takeover
        if not tk:
            return None
        until = parse_time_ms(tk.get("until"))
        if until is not None and until <= (now if now is not None else self.clock.now()):
            return None
        return tk

    def _refresh_known(self) -> None:
        urls: set[str] = set()
        items = list(self.items.values())
        if self.takeover:
            items.append(self.takeover["item"])
        for item in items:
            media = item.get("media") or {}
            for key in ("original", "display", "poster"):
                if isinstance(media.get(key), str):
                    urls.add(media[key])
            for url in media.get("fallbacks") or []:
                if isinstance(url, str):
                    urls.add(url)
        self._known_urls = urls

    def show_sig(self) -> Optional[str]:
        s = self.show
        if not s:
            return None
        return f"{s['id']}@{s['version']}|{s['epochMs']}|{s['pausedAtMs']}"

    # Persistence ---------------------------------------------------------------------

    def _save_snapshot(self, only_if_offset_moved: bool = False) -> None:
        if self.mode != "player" or not self.device:
            return  # the sample is never this screen's own playlist
        if self.show is None:
            self.files.save_show(None)
            return
        offset = self.clock.wall_offset()
        if only_if_offset_moved and self._saved_offset is not None and abs(offset - self._saved_offset) < 2000:
            return  # nothing moved worth an SD card write
        self._saved_offset = offset
        self.files.save_show(
            {
                "baseUrl": self.cfg.base_url,
                "deviceId": self.device.get("deviceId"),
                "wallOffset": offset,
                "keepOffline": self.keep_offline,
                "status": self.status,
                "show": self.show,
            }
        )

    def _load_snapshot(self) -> None:
        snap = self.files.load_show()
        if not snap or not self.device or snap.get("baseUrl") != self.cfg.base_url or snap.get("deviceId") != self.device.get("deviceId"):
            return
        show = snap.get("show")
        if not isinstance(show, dict):
            return
        self.show = show
        self.show["settings"] = normalise_settings(show.get("settings"))
        self.items = {i["id"]: i for i in show.get("items") or [] if isinstance(i, dict) and "id" in i}
        self.keep_offline = bool(snap.get("keepOffline"))
        self.clock.seed(snap.get("wallOffset"))
        self._refresh_known()
        log.info("playing the stored playlist until the first sync")

    # Commands (SPEC §7) ---------------------------------------------------------------

    def handle_commands(self, commands: list) -> None:
        acted = False
        valid = [c for c in commands if isinstance(c, dict) and isinstance(c.get("seq"), int)]
        for command in sorted(valid, key=lambda c: c["seq"]):
            if command["seq"] <= self.ack_seq:
                continue  # carried out already: each command exactly once
            kind = command.get("type")
            if kind == "identify":
                name = (self.device_info or {}).get("name") or "This screen"
                self.event({"type": "event", "event": "identify", "name": name, "ms": 6000})
            elif kind == "reload":
                self.drop_playlist()
            elif kind == "clear-cache":
                # What is on the wall is already decoded in memory and stays up.
                self.store.clear(keep=self.in_use)
                self.event({"type": "event", "event": "clear-cache"})
            self.handled_commands.appendleft({"seq": command["seq"], "type": kind, "at": wall_ms()})
            self.ack_seq = command["seq"]  # unknown types are acknowledged too
            acted = True
        if acted:
            if self.device:
                self.device["ackSeq"] = self.ack_seq
                self.files.save_device(self.device)
            self._sync_soon = True  # acknowledge promptly, so the dashboard sees it done

    def drop_playlist(self) -> None:
        """`reload`: forget the playlist and ask for it again."""
        self.show = None
        self.items = {}
        self.force_full = True
        self.files.save_show(None)
        self._refresh_known()
        self.event({"type": "event", "event": "reload"})
        self.sync_soon()

    # Leaving ---------------------------------------------------------------------------

    def unlink_locally(self) -> None:
        """The credential stopped working (removed from the dashboard, or forgotten)."""
        self._cancel("sync")
        self.device = None
        self.device_info = None
        self.files.forget_all()
        self.store.drop_all()  # what was kept belongs to the account it was kept for
        self._clear_qr_dir()
        self.ack_seq = 0
        self.reported_durations.clear()
        self.pending_durations.clear()
        self.play_log.clear()
        self.keep_offline = False
        self.last_error = None
        self.start_pairing()

    async def forget(self) -> None:
        """"Forget this account" from the device's own menu (SPEC §10)."""
        device = self.device
        if device:
            try:
                await self.api.forget(device["token"])
            except NetworkError:
                log.info("could not tell SolPersona; forgetting locally")
        self.unlink_locally()

    # ------------------------------------------------------------------------
    # From the player
    # ------------------------------------------------------------------------

    def on_player_message(self, msg: Any) -> None:
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if kind == "report":
            state = msg.get("state")
            self.player_report = state if isinstance(state, dict) else {}
            self.player_seen = mono_ms()
            in_use = msg.get("inUse")
            self.in_use = [u for u in in_use if isinstance(u, str)][:20] if isinstance(in_use, list) else []
            self._touch_alive()
        elif kind == "duration":
            self.record_duration(msg.get("itemId"), msg.get("ms"))
        elif kind == "action":
            action = msg.get("action")
            if action == "sample":
                self.start_sample()
            elif action == "stop-sample":
                self.stop_sample()
            elif action == "forget":
                asyncio.ensure_future(self.forget())
            elif action == "reload" and self.mode == "player":
                self.drop_playlist()
            elif isinstance(action, str) and action.startswith("wifi-"):
                self._wifi_action(action, msg)
        elif kind == "debug":
            self.debug_watchers = 1 if msg.get("on") else 0
            self.changed()

    def _wifi_action(self, action: str, msg: dict) -> None:
        """The screen's own Wi-Fi panel (keyboard or remote): scan, join, country, setup network."""
        net = self.network
        if net is None or not getattr(net, "enabled", False):
            return
        if action == "wifi-scan":
            asyncio.ensure_future(net.rescan())
        elif action == "wifi-join":
            ssid = msg.get("ssid")
            password = msg.get("password") or ""
            if isinstance(ssid, str) and 0 < len(ssid.encode("utf-8")) <= 32 and isinstance(password, str) and len(password) <= 63:
                asyncio.ensure_future(net.join(ssid, password))
        elif action == "wifi-country":
            if net.set_country(str(msg.get("country") or "")):
                self.changed()
        elif action == "wifi-setup":
            asyncio.ensure_future(net.start_hotspot(manual=True))

    def record_duration(self, item_id: Any, ms: Any) -> None:
        """A video's measured length, once per item per show version (SPEC §6.4)."""
        if self.mode != "player" or not isinstance(item_id, str):
            return
        if not isinstance(ms, (int, float)) or isinstance(ms, bool) or not 0 < ms < 86_400_000:
            return
        in_play = item_id in self.items or (self.takeover and self.takeover["item"].get("id") == item_id)
        if not in_play:
            return
        key = f"{self.show['id']}@{self.show['version']}|{item_id}" if self.show else f"takeover|{item_id}"
        if key in self.reported_durations:
            return
        self.reported_durations.add(key)
        self.pending_durations[item_id] = int(round(ms))
        self.sync_soon()  # a late report can land after the estimated slot has ended

    def _touch_alive(self) -> None:
        """The kiosk's watchdog reads this: a page that stops reporting gets Chromium restarted.
        Seconds since boot, not a file time — an NTP step must not look like a hang."""
        now = time.monotonic()
        if now - self._last_alive_touch < 5:
            return
        self._last_alive_touch = now
        try:
            path = self.cfg.run_dir / "player-alive"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{int(now)}\n")
        except OSError:
            pass

    # Offline plays (SPEC §4.1 `plays`) ---------------------------------------------

    def record_offline_play(self) -> None:
        """While polls fail, write down what is visibly on the wall, in server time."""
        if self.mode != "player" or self.online or self.dark or not self.player_connected():
            return
        r = self.player_report
        item_id = r.get("itemId")
        if not item_id or r.get("visible") is False:
            return
        now = self.clock.now()
        last = self.play_log[-1] if self.play_log else None
        if last and last["itemId"] == item_id and now - last["end"] < 5000:
            last["end"] = now
        elif len(self.play_log) < 2000:
            self.play_log.append({"itemId": item_id, "startedAt": now, "end": now})
        self.plays_dirty = True

    def _plays_to_send(self) -> tuple[int, list[dict]]:
        """(entries taken, what to send) — at most 2000 (SPEC §4.1)."""
        taken = min(len(self.play_log), 2000)
        if not taken:
            return 0, []
        out = [
            {"itemId": e["itemId"], "startedAt": round(e["startedAt"]), "ms": round(e["end"] - e["startedAt"])}
            for e in self.play_log[:taken]
            if e["end"] > e["startedAt"]
        ]
        self._sent_last_end = self.play_log[taken - 1]["end"]
        return taken, out

    def _plays_reported(self, taken: int) -> None:
        """What was sent is the server's now. If the last stretch went on growing while
        the request was out, the part after what was sent stays, as a stretch of its own."""
        last = self.play_log[taken - 1]
        rest = None
        if last["end"] > self._sent_last_end:
            rest = {"itemId": last["itemId"], "startedAt": self._sent_last_end, "end": last["end"]}
        del self.play_log[:taken]
        if rest:
            self.play_log.insert(0, rest)
        self.plays_dirty = True
        self._save_plays(force=True)

    def _save_plays(self, force: bool = False) -> None:
        if self.plays_dirty or force:
            self.plays_dirty = False
            try:
                self.files.save_plays([p for p in self.play_log if p["end"] > p["startedAt"]])
            except OSError as error:
                log.warning("could not save plays: %s", error)

    # ------------------------------------------------------------------------
    # Capabilities, power, the store
    # ------------------------------------------------------------------------

    def cap_bytes(self) -> int:
        auto = free_room(self.cfg.cache_dir, self.store.total_bytes() + self.store.in_flight_bytes())
        if self.cfg.cache_mb > 0:
            return min(self.cfg.cache_mb * MiB, auto)
        return auto

    def capabilities(self) -> dict:
        if self._caps is None:
            self._caps = self._measure_caps()
        return self._caps

    def _measure_caps(self) -> dict:
        size = self.display.logical() or (1920, 1080)
        return {
            "screenWidth": min(20000, size[0]),
            "screenHeight": min(20000, size[1]),
            "storageMb": min(10_000_000, self.cap_bytes() // MiB),
            "video": True,
            "originals": self.display.can_hold_originals(),
            "persistentStorage": True,
        }

    def refresh_capabilities(self) -> None:
        before = self._caps
        self.display.refresh()
        self.store.drop_smaller_copies(self.display.logical())
        caps = self._measure_caps()
        if before is not None and before.get("storageMb"):
            # Storage moves with every download; only a real change is news.
            if abs(caps["storageMb"] - before["storageMb"]) < max(64, before["storageMb"] // 20):
                caps["storageMb"] = before["storageMb"]
        self._caps = caps

    def wants(self) -> list[Want]:
        """What the screen should hold, most urgent first."""
        caps = self.capabilities()
        width, originals = caps["screenWidth"], caps["originals"]
        out: list[Want] = []
        tk = self.active_takeover()
        if tk and self.mode == "player" and self.status != "UNLICENSED":
            item = tk["item"]
            out.append(self._want(item, tk["settings"], 0, "takeover", width, originals))
        show = self.show
        if self.mode != "player" or not show or not show["timeline"] or self.status == "UNLICENSED":
            return out
        pos = position_at(show["timeline"], show, self.clock.now())
        start = pos["index"] if pos else 0
        n = len(show["timeline"])
        seen: set[str] = set()
        for i in range(n):
            item = self.items.get(show["timeline"][(start + i) % n]["id"])
            if not item or item["id"] in seen:
                continue
            seen.add(item["id"])
            out.append(self._want(item, show["settings"], len(out), "show", width, originals))
        return out

    @staticmethod
    def _want(item: dict, settings: dict, rank: int, role: str, width: int, originals: bool) -> Want:
        kind = "VIDEO" if item.get("kind") == "VIDEO" else "IMAGE"
        urls = choose_candidates(item, settings, width, originals)
        # The small still beside the piece: a film's poster and wash, or — for a
        # still shown from its original — the copy the BLUR wash is drawn from.
        still = video_still(item)
        if still and urls and still == urls[0]:
            still = None
        return Want(item_id=item["id"], urls=urls, rank=rank, kind=kind, still=still, role=role)

    def should_be_dark(self) -> bool:
        """Outside opening hours, with no takeover (a takeover is still shown)."""
        if self.mode != "player" or not self.show or self.status == "UNLICENSED":
            return False
        if self.active_takeover():
            return False
        return not schedule_open(self.show["settings"].get("schedule"), local_minutes(self.clock.now()))

    async def apply_power(self) -> None:
        """Switch the panel in the background: CEC and wlr-randr can take many
        seconds, and the housekeeping loop must keep petting systemd's watchdog."""
        if self._power_task and not self._power_task.done():
            return
        dark = self.should_be_dark()
        if dark != self.dark or self.display.powered is None:
            self.dark = dark
            self.changed()

            async def switch(on: bool) -> None:
                try:
                    await self.display.set_power(on)
                except Exception:
                    log.exception("could not switch the panel")

            self._power_task = asyncio.ensure_future(switch(not dark))

    def _stored_changed(self) -> None:
        self.stored_version += 1
        self.changed()

    def _update_store(self) -> None:
        sig = f"{self.show['id']}@{self.show['version']}" if self.mode == "player" and self.show else None
        self.store.update(self.wants(), self.keep_offline, self.in_use, sig)
        if sig and sig != self._show_sig_pruned:
            self._show_sig_pruned = sig
            self._prune_qr(sig)

    # ------------------------------------------------------------------------
    # Housekeeping, once a second
    # ------------------------------------------------------------------------

    async def _housekeeping(self) -> None:
        last_caps = last_status = last_plays = 0.0
        while True:
            try:
                now = time.monotonic()
                self.record_offline_play()
                await self.apply_power()
                self._update_store()
                self.prefetch_qr()
                if now - last_caps > 60:
                    last_caps = now
                    self.refresh_capabilities()
                if now - last_plays > 30:
                    last_plays = now
                    self._save_plays()
                if now - last_status > 5:
                    last_status = now
                    self._write_status()
                self.broadcast()
                if self.on_tick:
                    self.on_tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("housekeeping")
            await asyncio.sleep(self.timing.tick_s)

    def _write_status(self) -> None:
        """For the updater: which version runs, and when it last synced (SPEC: roll back if it never does)."""
        try:
            atomic_write_json(
                self.cfg.run_dir / "status.json",
                {
                    "appVersion": self.version,
                    "mode": self.mode,
                    "lastSyncOkAt": self.last_sync_ok_wall,
                    "dark": self.dark,
                    "online": self.online,
                    "updatedAt": wall_ms(),
                },
                mode=0o644,
            )
        except OSError:
            pass

    # ------------------------------------------------------------------------
    # To the player
    # ------------------------------------------------------------------------

    def changed(self) -> None:
        """Something the player should know: send it now (coalesced), not at the next tick."""
        if self._broadcast_pending:
            return
        self._broadcast_pending = True
        try:
            asyncio.get_running_loop().call_soon(self.broadcast)
        except RuntimeError:
            self._broadcast_pending = False

    def event(self, message: dict) -> None:
        for sub in list(self.subscribers):
            sub.put(message)

    def subscribe(self) -> Subscriber:
        sub = Subscriber()
        self.subscribers.add(sub)
        self.broadcast()
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self.subscribers.discard(sub)

    def broadcast(self) -> None:
        self._broadcast_pending = False
        if not self.subscribers:
            return
        state = self.player_state()
        sig = self.show_sig()
        for sub in list(self.subscribers):
            if sub.show_sig != sig:
                sub.put({"type": "show", "show": self.show})
                sub.show_sig = sig
            if sub.stored_version != self.stored_version:
                sub.put({"type": "stored", "urls": self.store.stored_urls()})
                sub.stored_version = self.stored_version
            sub.put(state)

    def player_state(self) -> dict:
        stored, total = self.store.counts()
        state = {
            "type": "state",
            "mode": self.mode,
            "status": self.status,
            "device": self.device_info,
            "registration": self.public_registration(),
            "pairError": self.pair_error,
            "showSig": self.show_sig(),
            "takeover": self.takeover,
            "serverNow": self.clock.now(),
            "clockSynced": self.clock.synced,
            "online": self.online,
            "lastError": self.last_error,
            "capabilities": self.capabilities(),
            "kept": {
                "on": self.keep_offline, "stored": stored, "total": total, "error": self.store.error,
                "downloads": self.store.downloads(), "mb": round(self.store.total_bytes() / MiB, 1),
            },
            "dark": self.dark,
            "network": self.network.public() if self.network else None,
            "appVersion": self.version,
            "debugAllowed": bool(self.cfg.debug),
            "baseHost": host_of(self.cfg.base_url),
            "baseUrl": self.cfg.base_url,
        }
        if self.debug_watchers:
            state["debug"] = self.debug_state()
        return state

    def debug_state(self) -> dict:
        now = mono_ms()
        store = self.store
        states: dict[str, int] = {}
        for st in store.items.values():
            states[st.state] = states.get(st.state, 0) + 1
        return {
            "offset": self.clock.wall_offset() if self.clock.synced else None,
            "rtt": self.clock.rtt,
            "lastSyncAgoMs": now - self.last_sync_at if self.last_sync_at else None,
            "nextSyncInMs": max(0, self.next_sync_at - now) if self.next_sync_at else None,
            "failures": self.failures,
            "pollMs": self.poll_ms,
            "ackSeq": self.ack_seq,
            "commands": list(self.handled_commands),
            "pendingDurations": dict(self.pending_durations),
            "plays": len(self.play_log),
            "deviceId": (self.device or {}).get("deviceId") or (self.registration or {}).get("deviceId"),
            "calls": list(self.api.calls)[:40],
            "store": {
                "files": len(store.files),
                "mb": round(store.total_bytes() / MiB, 1),
                "capMb": self.capabilities()["storageMb"],
                "running": list(store.running),
                "states": states,
                "roomFull": store.room_full,
            },
            "panel": self.display.panel,
            "power": self.display.method,
        }

    # ------------------------------------------------------------------------
    # Media and QR codes, for the server
    # ------------------------------------------------------------------------

    def media(self, url: str) -> tuple[Optional[Path], Optional[str], bool]:
        """(file on the card, its type, whether this URL belongs to what the screen plays)."""
        hit = self.store.path_for(url)
        if hit:
            return hit[0], hit[1], True
        return None, None, url in self._known_urls

    async def qr(self, item_id: str, sample: bool) -> tuple[str, Optional[bytes]]:
        """('ok', png) | ('none', None) | ('error', None). SPEC §9 / §11b."""
        if sample:
            if self.mode != "sample" or item_id not in self.items:
                return "none", None
            if item_id in self.qr_memory:
                png = self.qr_memory[item_id]
                return ("ok", png) if png else ("none", None)
            try:
                r = await self.api.sample_qr(item_id)
            except NetworkError:
                return "error", None
            if r.ok and r.body:
                self.qr_memory[item_id] = r.body
                return "ok", r.body
            if r.status == 404:
                self.qr_memory[item_id] = None
                return "none", None
            return "error", None

        if self.mode != "player" or not self.device:
            return "none", None
        tk = self.active_takeover()
        in_takeover = bool(tk and tk["item"].get("id") == item_id)
        if item_id not in self.items and not in_takeover:
            return "none", None  # only pieces in the show or takeover are answered
        settings = tk["settings"] if in_takeover else (self.show or {}).get("settings") or {}
        sig = f"{self.show['id']}@{self.show['version']}" if self.show else "takeover"
        name = f"{url_key(sig)[:12]}-{url_key(settings.get('qrTarget', '') + '|' + item_id)}"
        png_path, none_path = self.qr_dir / (name + ".png"), self.qr_dir / (name + ".none")
        if png_path.is_file():
            return "ok", png_path.read_bytes()
        if none_path.exists():
            return "none", None
        try:
            r = await self.api.qr(self.device["token"], item_id)
        except NetworkError:
            self._qr_retry_at = mono_ms() + 60000
            return "error", None
        if r.ok and r.body:
            try:
                atomic_write_bytes(png_path, r.body, mode=0o644)
            except OSError:
                pass
            return "ok", r.body
        if r.status == 404:
            try:
                atomic_write_bytes(none_path, b"", mode=0o644)
            except OSError:
                pass
            return "none", None
        if r.status == 401 and r.code in UNLINKED_CODES:
            self.unlink_locally()
            return "error", None
        # 429 or a server error: no more prefetching until it has had a rest.
        self._qr_retry_at = mono_ms() + (r.retry_after_ms if r.retry_after_ms is not None else 60000)
        return "error", None

    def prefetch_qr(self) -> None:
        """Every piece's code onto the card, one at a time while online, so the
        codes on the wall survive a reboot with no network as the pieces do."""
        if self.mode != "player" or not self.online or not self.show or self._qr_task_busy:
            return
        if mono_ms() < self._qr_retry_at:
            return
        if self.show["settings"].get("qrTarget") == "NONE":
            return
        sig = f"{self.show['id']}@{self.show['version']}"
        target = self.show["settings"].get("qrTarget", "")
        for item_id in self.items:
            name = f"{url_key(sig)[:12]}-{url_key(target + '|' + item_id)}"
            if (self.qr_dir / (name + ".png")).exists() or (self.qr_dir / (name + ".none")).exists():
                continue
            self._qr_task_busy = True

            async def fetch(item_id: str = item_id) -> None:
                try:
                    await self.qr(item_id, sample=False)
                finally:
                    self._qr_task_busy = False

            asyncio.ensure_future(fetch())
            return

    def _prune_qr(self, sig: str) -> None:
        """Cached per show version (SPEC §9): another version's codes go."""
        prefix = url_key(sig)[:12] + "-"
        try:
            for path in self.qr_dir.iterdir():
                if not path.name.startswith(prefix) and not path.name.startswith(url_key("takeover")[:12]):
                    remove(path)
        except OSError:
            pass

    def _clear_qr_dir(self) -> None:
        self.qr_memory.clear()
        try:
            for path in self.qr_dir.iterdir():
                remove(path)
        except OSError:
            pass
