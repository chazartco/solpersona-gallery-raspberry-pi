"""The gallery kept on the card (SPEC §6.5, §11a).

The Pi always keeps the show on board: as soon as a show arrives, every piece
is downloaded in timeline order from the piece on the wall (two at a time,
nearest-due first) into /var/cache/solpersona-gallery/media, so the gallery
soon plays with no network at all, and across reboots. The wall never waits
for this: a piece not yet on disk is streamed (the player is redirected to its
real address) while the download catches up.

Room:
* The cap is the `cache_mb` setting, or — by default — most of the card's free
  space. It is what the screen reports as `capabilities.storageMb`.
* When a download needs room, files go in this order: files nothing wants any
  more, least recently used first; then, unless the owner switched on "Keep
  this gallery on the screen" (`keepOffline`), the show's own files that are
  due furthest away. With `keepOffline` the show's files are never evicted; a
  show that does not fit is stored from the wall onwards as far as it goes, and
  `state.error` says "Room for 31 of 40 pieces".
* A new show version keeps every file still in it, fetches what is new and
  deletes what is no longer used.

Files are written to `partial/`, fsynced and renamed into place, and the index
is rewritten whole the same way, so a power cut can lose at most the download
in flight. On start the index and the directory are reconciled.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

import aiohttp

from .util import atomic_write_json, fsync_dir, read_json, remove, url_key

log = logging.getLogger(__name__)

MiB = 1024 * 1024
MAX_PARALLEL = 2
MAX_FILE_BYTES = 4 * 1024 * MiB

EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "image/svg+xml": ".svg",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}


@dataclass
class StoredFile:
    url: str
    name: str
    bytes: int
    mime: str
    last_used: float
    derived_for: Optional[list[int]] = None  # [w, h] when this is a screen-sized copy of a larger original


@dataclass
class Want:
    """One piece the screen should hold, in the order it is due."""

    item_id: str
    urls: list[str]  # the files that would do, best first (SPEC §6.2)
    rank: int  # 0 = due now; higher = due later
    kind: str = "IMAGE"
    still: Optional[str] = None  # a film's still: the wash and the poster
    role: str = "show"  # show | takeover


@dataclass
class ItemState:
    state: str = "queued"  # queued | loading | stored | waiting | failed | room
    attempts: int = 0
    next_at: float = 0.0
    note: Optional[str] = None
    need: int = 0  # bytes it needed when refused for room


# ---------------------------------------------------------------------------
# Policy: pure, so the tests can hold it to the rules above.
# ---------------------------------------------------------------------------


def eviction_order(
    files: dict[str, StoredFile],
    wanted_rank: dict[str, int],
    protected: set[str],
    keep_offline: bool,
    below_rank: Optional[int] = None,
) -> list[str]:
    """URLs that may go to make room, in the order they should go.

    1. Files nothing wants, least recently used first.
    2. Unless `keep_offline`: wanted files due furthest away first — and only
       those due later than `below_rank`, so a download never evicts something
       more urgent than itself.
    Files on the wall (`protected`) never go.
    """
    unwanted = [u for u in files if u not in wanted_rank and u not in protected]
    unwanted.sort(key=lambda u: files[u].last_used)
    order = unwanted
    if not keep_offline:
        later = [
            u for u in files
            if u in wanted_rank and u not in protected and (below_rank is None or wanted_rank[u] > below_rank)
        ]
        later.sort(key=lambda u: (-wanted_rank[u], files[u].last_used))
        order = order + later
    return order


def plan_room(
    files: dict[str, StoredFile],
    cap_bytes: int,
    needed: int,
    wanted_rank: dict[str, int],
    protected: set[str],
    keep_offline: bool,
    for_rank: Optional[int],
) -> Optional[list[str]]:
    """The URLs to delete so `needed` more bytes fit under the cap — or None if they cannot."""
    total = sum(f.bytes for f in files.values())
    if total + needed <= cap_bytes:
        return []
    victims: list[str] = []
    for url in eviction_order(files, wanted_rank, protected, keep_offline, for_rank):
        victims.append(url)
        total -= files[url].bytes
        if total + needed <= cap_bytes:
            return victims
    return None


def stored_count(wants: Iterable[Want], has: Callable[[str], bool]) -> tuple[int, int]:
    """(pieces stored, pieces in the show). A piece counts once any of its files is kept —
    so a quality change still plays the copy already on the card."""
    stored = total = 0
    for want in wants:
        if want.role != "show":
            continue
        total += 1
        if any(has(u) for u in want.urls):
            stored += 1
    return stored, total


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

Optimiser = Callable[[Path, str], Optional[tuple[Path, str, list[int]]]]


class MediaStore:
    def __init__(
        self,
        root: Path,
        session: Callable[[], aiohttp.ClientSession],
        cap_bytes: Callable[[], int],
        optimiser: Optional[Optimiser] = None,
        clock: Callable[[], float] = time.time,
        max_parallel: int = MAX_PARALLEL,
    ) -> None:
        self.root = root
        self.dir = root / "media"
        self.partial = root / "partial"
        self.index_path = root / "index.json"
        self._session = session
        self._cap = cap_bytes
        self._optimiser = optimiser
        self._clock = clock
        self.max_parallel = max_parallel
        self.files: dict[str, StoredFile] = {}
        self.items: dict[str, ItemState] = {}
        self.running: dict[str, asyncio.Task] = {}
        self.wants: list[Want] = []
        self.keep_offline = False
        self.protected: set[str] = set()
        self.room_full = False
        self.error: Optional[str] = None
        self.show_sig: Optional[str] = None
        self.gen = 0
        self.on_change: Callable[[], None] = lambda: None
        self._index_dirty = False
        self._last_index_save = 0.0
        self._refused: dict[str, int] = {}  # item → bytes it needed when there was no room
        self._partial: dict[str, int] = {}  # url → bytes written so far
        self._last_need = 0

    # Start-up ------------------------------------------------------------------

    def load(self) -> None:
        """Make the index and the directory agree: a file without an entry, or an
        entry without a file, is what a power cut leaves behind."""
        self.dir.mkdir(parents=True, exist_ok=True)
        self.partial.mkdir(parents=True, exist_ok=True)
        for leftover in self.partial.iterdir():
            remove(leftover)
        raw = read_json(self.index_path, {})
        files: dict[str, StoredFile] = {}
        if isinstance(raw, dict):
            for url, entry in (raw.get("files") or {}).items():
                try:
                    f = StoredFile(**entry)
                except TypeError:
                    continue
                path = self.dir / f.name
                if path.is_file() and path.stat().st_size == f.bytes:
                    files[url] = f
        names = {f.name for f in files.values()}
        for path in self.dir.iterdir():
            if path.name not in names:
                remove(path)
        self.files = files
        self._save_index(force=True)

    def _save_index(self, force: bool = False) -> None:
        now = self._clock()
        if not force and (not self._index_dirty or now - self._last_index_save < 30):
            return
        atomic_write_json(self.index_path, {"files": {u: asdict(f) for u, f in self.files.items()}}, mode=0o644)
        self._index_dirty = False
        self._last_index_save = now

    def flush(self) -> None:
        if self._index_dirty:
            self._save_index(force=True)

    # Reading ---------------------------------------------------------------------

    def has(self, url: str) -> bool:
        return url in self.files

    def path_for(self, url: str) -> Optional[tuple[Path, str]]:
        f = self.files.get(url)
        if not f:
            return None
        path = self.dir / f.name
        if not path.is_file():
            self._forget(url)
            return None
        f.last_used = self._clock()
        self._index_dirty = True
        return path, f.mime

    def stored_urls(self) -> list[str]:
        return list(self.files)

    def total_bytes(self) -> int:
        return sum(f.bytes for f in self.files.values())

    def in_flight_bytes(self) -> int:
        """Bytes already written to downloads not yet finished: ours too, when the
        cap is measured from the card's free space."""
        return sum(self._partial.values())

    def counts(self) -> tuple[int, int]:
        return stored_count(self.wants, self.has)

    def wanted_rank(self) -> dict[str, int]:
        ranks: dict[str, int] = {}
        for want in self.wants:
            for url in [*want.urls, *([want.still] if want.still else [])]:
                if url not in ranks or want.rank < ranks[url]:
                    ranks[url] = want.rank
        return ranks

    # What to hold ------------------------------------------------------------------

    def update(self, wants: list[Want], keep_offline: bool, protected: Iterable[str], show_sig: Optional[str]) -> None:
        """Called every second with what the screen should hold, nearest-due first."""
        self.wants = wants
        self.keep_offline = keep_offline
        self.protected = set(protected)
        if show_sig is not None and show_sig != self.show_sig:
            self.show_sig = show_sig
            self._prune()
        self.pump()
        self._save_index()

    def _prune(self) -> None:
        """A new show (or version): delete what it no longer uses; forget old failures."""
        ranks = self.wanted_rank()
        in_show = {w.item_id for w in self.wants}
        for item_id in list(self.items):
            if item_id not in in_show or self.items[item_id].state != "loading":
                del self.items[item_id]
        self.room_full = False
        unused = [u for u in self.files if u not in ranks and u not in self.protected]
        for url in unused:
            self._delete(url)
        if unused:
            log.info("deleted %d files the new show no longer uses", len(unused))
            self._save_index(force=True)
            self.on_change()

    def pump(self) -> None:
        now = self._clock()
        cap = self._cap()
        ranks = self.wanted_rank()
        self.room_full = self.total_bytes() >= cap or any(st.state == "room" for st in self.items.values())
        for want in self.wants:
            if len(self.running) >= self.max_parallel:
                break
            if want.item_id in self.running or any(self.has(u) for u in want.urls):
                continue
            st = self.items.get(want.item_id)
            if st and st.state == "room":
                # Refused for room before: try again when something less urgent could
                # make way for it (the wall moved on) — after a minute at the soonest.
                if st.next_at > now:
                    continue
                if plan_room(self.files, cap, max(1, st.need), ranks, self.protected, self.keep_offline, want.rank) is None:
                    continue
            elif st and st.next_at > now:
                continue
            if self.total_bytes() >= cap and plan_room(
                self.files, cap, 1, ranks, self.protected, self.keep_offline, want.rank
            ) is None:
                self.room_full = True
                break
            self._start(want)
        stored, total = self.counts()
        if self.room_full and stored < total:
            self.error = f"Room for {stored} of {total} pieces"
        else:
            self.error = None

    def _start(self, want: Want) -> None:
        st = self.items.setdefault(want.item_id, ItemState())
        st.state = "loading"
        gen = self.gen
        task = asyncio.ensure_future(self._store_item(want, gen))
        self.running[want.item_id] = task

        def done(_task: asyncio.Task) -> None:
            if self.running.get(want.item_id) is task:
                del self.running[want.item_id]

        task.add_done_callback(done)

    async def _store_item(self, want: Want, gen: int) -> str:
        outcome = "failed"
        for url in want.urls:
            outcome = await self.download(url, want.rank, gen, want.kind, item_id=want.item_id)
            if outcome in ("stored", "offline", "room", "gone"):
                break  # 'failed': the next copy may live on a friendlier host
        if gen != self.gen:
            return "gone"
        st = self.items.setdefault(want.item_id, ItemState())
        now = self._clock()
        if outcome == "stored":
            st.state, st.note = "stored", None
            if want.still and not self.has(want.still):
                await self.download(want.still, want.rank, gen, "IMAGE")  # best effort: the wash, offline
        else:
            st.attempts += 1
            if outcome == "room":
                st.state, st.need = "room", self._refused.pop(want.item_id, 0)
                st.next_at = now + 60  # never a tight loop, whatever the room arithmetic says
            else:
                st.state = "waiting" if outcome == "offline" else "failed"
                st.next_at = now + min(600, 30 * 2 ** (st.attempts - 1))
        self.on_change()
        return outcome

    async def download(self, url: str, rank: int, gen: int, kind: str = "IMAGE", item_id: str = "") -> str:
        """One URL onto the card. → stored | failed | offline | room | gone"""
        if self.has(url):
            return "stored"
        try:
            outcome = await self._download(url, rank, gen, kind)
        finally:
            self._partial.pop(url, None)
        if outcome == "room" and item_id:
            self._refused[item_id] = self._last_need
        return outcome

    async def _download(self, url: str, rank: int, gen: int, kind: str) -> str:
        tmp = self.partial / (url_key(url) + ".part")
        size = 0
        mime = ""
        try:
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=60)
            async with self._session().get(url, timeout=timeout) as res:
                if res.status != 200:
                    log.info("download %s: HTTP %s", short(url), res.status)
                    return "failed"
                length = res.content_length or 0
                if length > MAX_FILE_BYTES:
                    return "failed"
                if not self._make_room(length, rank):
                    return "room"
                mime = (res.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                with open(tmp, "wb") as handle:
                    async for chunk in res.content.iter_chunked(1 << 16):
                        if gen != self.gen:
                            remove(tmp)
                            return "gone"
                        size += len(chunk)
                        if size > MAX_FILE_BYTES:
                            raise ValueError("too large")
                        handle.write(chunk)
                        self._partial[url] = size
                    handle.flush()
                    await asyncio.get_running_loop().run_in_executor(None, os.fsync, handle.fileno())
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            remove(tmp)
            log.info("download %s: %s", short(url), type(error).__name__)
            return "offline"
        except ValueError:
            remove(tmp)
            return "failed"
        except OSError as error:
            remove(tmp)
            log.warning("download %s: %s", short(url), error)
            return "room" if error.errno == 28 else "failed"  # ENOSPC
        if gen != self.gen:
            remove(tmp)
            return "gone"
        if not mime or mime == "application/octet-stream":
            mime = mimetypes.guess_type(url.split("?")[0])[0] or "application/octet-stream"
        derived_for: Optional[list[int]] = None
        if kind == "IMAGE" and self._optimiser is not None and mime.startswith("image/"):
            try:
                result = await asyncio.get_running_loop().run_in_executor(None, self._optimiser, tmp, mime)
            except Exception as error:  # a picture the optimiser cannot read is kept as it came
                log.info("not resizing %s: %s", short(url), error)
                result = None
            if result:
                new_path, mime, derived_for = result
                if new_path != tmp:
                    os.replace(new_path, tmp)
                size = tmp.stat().st_size
        if not self._make_room(size, rank):
            remove(tmp)
            return "room"
        name = url_key(url) + EXTENSIONS.get(mime, mimetypes.guess_extension(mime) or ".bin")
        os.replace(tmp, self.dir / name)
        fsync_dir(self.dir)
        if gen != self.gen:
            remove(self.dir / name)
            return "gone"
        self.files[url] = StoredFile(url=url, name=name, bytes=size, mime=mime, last_used=self._clock(), derived_for=derived_for)
        self._save_index(force=True)
        log.info("stored %s (%d KB)", short(url), size // 1024)
        return "stored"

    def _make_room(self, needed: int, rank: int) -> bool:
        victims = plan_room(self.files, self._cap(), needed, self.wanted_rank(), self.protected, self.keep_offline, rank)
        if victims is None:
            self.room_full = True
            self._last_need = needed
            return False
        for url in victims:
            self._delete(url)
        if victims:
            self._save_index(force=True)
            self.on_change()
        return True

    def _delete(self, url: str) -> None:
        f = self.files.pop(url, None)
        if f:
            remove(self.dir / f.name)
            self._index_dirty = True

    def _forget(self, url: str) -> None:
        self._delete(url)
        self.on_change()

    def drop_smaller_copies(self, size: Optional[tuple[int, int]]) -> None:
        """A larger panel was plugged in: copies sized for the old one are too small now."""
        if not size:
            return
        stale = [
            u for u, f in self.files.items()
            if f.derived_for and (size[0] > f.derived_for[0] or size[1] > f.derived_for[1]) and u not in self.protected
        ]
        for url in stale:
            self._delete(url)
        if stale:
            log.info("a larger screen: fetching %d originals again", len(stale))
            self._save_index(force=True)
            self.on_change()

    # Commands ------------------------------------------------------------------------

    def clear(self, keep: Iterable[str] = ()) -> None:
        """`clear-cache`: everything goes except what is on the wall; downloads start again."""
        keep_set = set(keep)
        self._cancel()
        for url in [u for u in self.files if u not in keep_set]:
            self._delete(url)
        self._save_index(force=True)
        self.on_change()

    def drop_all(self) -> None:
        """Unlinked or forgotten: what was kept belongs to the account it was kept for."""
        self._cancel()
        for url in list(self.files):
            self._delete(url)
        self.wants = []
        self.show_sig = None
        self._save_index(force=True)
        self.on_change()

    def _cancel(self) -> None:
        self.gen += 1
        for task in self.running.values():
            task.cancel()
        self.running.clear()
        self.items.clear()
        self.room_full = False
        self.error = None

    async def close(self) -> None:
        self._cancel()
        self.flush()


def short(url: str) -> str:
    """A URL for a log line: host and the end of the path, never a query string."""
    try:
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        path = parts.path if len(parts.path) <= 40 else "…" + parts.path[-38:]
        return parts.netloc + path
    except ValueError:
        return url[:48]


def free_room(path: Path, used_by_us: int, reserve_floor: int = 1024 * MiB) -> int:
    """How much the store may use when `cache_mb` is automatic: what it holds now plus
    the free space, less a reserve so the card is never filled to the brim."""
    try:
        st = os.statvfs(path)
    except OSError:
        return 0
    size = st.f_frsize * st.f_blocks
    free = st.f_frsize * st.f_bavail
    reserve = max(reserve_floor, size // 20)
    return max(0, used_by_us + free - reserve)
