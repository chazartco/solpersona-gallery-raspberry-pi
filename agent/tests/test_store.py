"""The disk store: LRU, the size cap, keepOffline, new versions, power cuts."""

import asyncio
import json

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from solpersona_gallery.imageopt import cover_size, make_optimiser
from solpersona_gallery.store import MediaStore, StoredFile, Want, eviction_order, plan_room, stored_count

KB = 1024


def f(url, size, used):
    return StoredFile(url=url, name=url + ".bin", bytes=size, mime="image/jpeg", last_used=used)


# Policy ----------------------------------------------------------------------------


def test_unwanted_files_go_first_least_recently_used_first():
    files = {"old": f("old", 10, 1), "older": f("older", 10, 0), "show": f("show", 10, 0), "recent": f("recent", 10, 5)}
    order = eviction_order(files, {"show": 0}, protected=set(), keep_offline=True)
    assert order == ["older", "old", "recent"]


def test_without_keep_offline_the_furthest_due_show_files_go_next():
    files = {"a": f("a", 10, 0), "b": f("b", 10, 0), "c": f("c", 10, 0), "x": f("x", 10, 9)}
    ranks = {"a": 0, "b": 1, "c": 2}
    assert eviction_order(files, ranks, set(), keep_offline=False) == ["x", "c", "b", "a"]
    # A download never evicts something more urgent than itself.
    assert eviction_order(files, ranks, set(), keep_offline=False, below_rank=1) == ["x", "c"]


def test_the_wall_is_never_evicted():
    files = {"on-wall": f("on-wall", 10, 0), "other": f("other", 10, 1)}
    assert eviction_order(files, {}, protected={"on-wall"}, keep_offline=False) == ["other"]


def test_room_is_planned_under_the_cap():
    files = {"a": f("a", 40, 0), "b": f("b", 40, 1)}
    assert plan_room(files, cap_bytes=100, needed=10, wanted_rank={}, protected=set(), keep_offline=False, for_rank=0) == []
    assert plan_room(files, cap_bytes=100, needed=30, wanted_rank={}, protected=set(), keep_offline=False, for_rank=0) == ["a"]
    # keepOffline: the show's own files are never evicted — the download is refused instead.
    ranks = {"a": 0, "b": 1}
    assert plan_room(files, cap_bytes=100, needed=30, wanted_rank=ranks, protected=set(), keep_offline=True, for_rank=2) is None


def test_stored_count_counts_any_copy():
    wants = [Want("1", ["O1", "D1"], 0), Want("2", ["O2"], 1), Want("t", ["T"], 0, role="takeover")]
    assert stored_count(wants, lambda u: u in {"D1", "T"}) == (1, 2)


# The store, downloading from a local host ------------------------------------------


class Host:
    def __init__(self, sizes):
        self.sizes = sizes
        self.hits = []

    def app(self):
        app = web.Application()

        async def serve(request):
            name = request.match_info["name"]
            self.hits.append(name)
            if name not in self.sizes:
                return web.Response(status=404)
            return web.Response(body=b"x" * self.sizes[name], content_type="image/jpeg")

        app.router.add_get("/{name}", serve)
        return app


async def make_store(tmp_path, host, cap):
    server = TestServer(host.app())
    await server.start_server()
    base = str(server.make_url("")).rstrip("/")
    session = aiohttp.ClientSession()
    clock = [1000.0]
    store = MediaStore(tmp_path, session=lambda: session, cap_bytes=lambda: cap[0], clock=lambda: clock[0])
    store.load()
    return server, session, store, base, clock


async def settle(store):
    for _ in range(200):
        store.pump()
        if not store.running:
            store.pump()
            if not store.running:
                return
        await asyncio.sleep(0.01)


def test_downloads_every_piece_nearest_due_first(tmp_path):
    async def main():
        host = Host({f"p{i}.jpg": 10 * KB for i in range(5)})
        server, session, store, base, _ = await make_store(tmp_path, host, [10 ** 9])
        try:
            wants = [Want(f"i{i}", [f"{base}/p{(i + 2) % 5}.jpg"], i) for i in range(5)]
            store.update(wants, keep_offline=False, protected=[], show_sig="s@1")
            await settle(store)
            assert store.counts() == (5, 5)
            assert host.hits[:2] == ["p2.jpg", "p3.jpg"]  # from the wall onwards
            assert store.error is None
        finally:
            await session.close()
            await server.close()

    asyncio.run(main())


def test_the_small_copy_comes_first_and_the_original_still_follows(tmp_path):
    async def main():
        host = Host({"original.jpg": 50 * KB, "display.webp": 2 * KB})
        server, session, store, base, _ = await make_store(tmp_path, host, [10 ** 9])
        try:
            o, d = f"{base}/original.jpg", f"{base}/display.webp"
            want = Want("i", [o, d], 0, still=d)
            store.update([want], False, [], "s@1")
            await settle(store)
            assert host.hits == ["display.webp", "original.jpg"]
            assert store.has(o) and store.has(d) and store.complete(want)
        finally:
            await session.close()
            await server.close()

    asyncio.run(main())


def test_a_small_copy_alone_does_not_end_the_download(tmp_path):
    store = MediaStore(tmp_path, session=lambda: None, cap_bytes=lambda: 10 ** 9)
    store.load()
    store.files["d"] = f("d", 10, 0)
    assert not store.complete(Want("i", ["o", "d"], 0, still="d"))
    store.files["o"] = f("o", 10, 0)
    assert store.complete(Want("i", ["o", "d"], 0, still="d"))


def test_failed_copy_falls_back_to_the_next(tmp_path):
    async def main():
        host = Host({"display.webp": 5 * KB})
        server, session, store, base, _ = await make_store(tmp_path, host, [10 ** 9])
        try:
            store.update([Want("i", [f"{base}/missing.jpg", f"{base}/display.webp"], 0)], False, [], "s@1")
            await settle(store)
            assert store.has(f"{base}/display.webp") and store.counts() == (1, 1)
        finally:
            await session.close()
            await server.close()

    asyncio.run(main())


def test_keep_offline_stores_what_fits_and_says_so(tmp_path):
    async def main():
        host = Host({f"p{i}.jpg": 40 * KB for i in range(5)})
        server, session, store, base, _ = await make_store(tmp_path, host, [100 * KB])
        try:
            wants = [Want(f"i{i}", [f"{base}/p{i}.jpg"], i) for i in range(5)]
            store.update(wants, keep_offline=True, protected=[], show_sig="s@1")
            await settle(store)
            assert store.counts() == (2, 5)
            assert store.has(f"{base}/p0.jpg") and store.has(f"{base}/p1.jpg")
            assert store.error == "Room for 2 of 5 pieces"
            assert store.total_bytes() <= 100 * KB
        finally:
            await session.close()
            await server.close()

    asyncio.run(main())


def test_without_keep_offline_the_window_rolls_with_the_wall(tmp_path):
    async def main():
        host = Host({f"p{i}.jpg": 40 * KB for i in range(4)})
        server, session, store, base, clock = await make_store(tmp_path, host, [100 * KB])
        try:
            urls = [f"{base}/p{i}.jpg" for i in range(4)]
            store.update([Want(f"i{i}", [urls[i]], i) for i in range(4)], False, [], "s@1")
            await settle(store)
            assert {u for u in urls if store.has(u)} == {urls[0], urls[1]}
            # The wall moved on two pieces: what is due soonest now replaces what is due last.
            order = [2, 3, 0, 1]
            n = len(host.hits)
            store.pump()
            await asyncio.sleep(0.05)
            assert len(host.hits) == n  # a refused piece never retries in a tight loop
            clock[0] += 61
            store.update([Want(f"i{i}", [urls[i]], rank) for rank, i in enumerate(order)], False, [], "s@1")
            await settle(store)
            assert {u for u in urls if store.has(u)} == {urls[2], urls[3]}
        finally:
            await session.close()
            await server.close()

    asyncio.run(main())


def test_a_new_version_keeps_what_is_used_and_deletes_the_rest(tmp_path):
    async def main():
        host = Host({"a.jpg": 1 * KB, "b.jpg": 1 * KB, "c.jpg": 1 * KB})
        server, session, store, base, _ = await make_store(tmp_path, host, [10 ** 9])
        try:
            a, b, c = (f"{base}/{n}.jpg" for n in "abc")
            store.update([Want("A", [a], 0), Want("B", [b], 1)], False, [], "s@1")
            await settle(store)
            hits = len(host.hits)
            store.update([Want("B", [b], 0), Want("C", [c], 1)], False, [], "s@2")
            await settle(store)
            assert not store.has(a) and store.has(b) and store.has(c)
            assert host.hits[hits:] == ["c.jpg"]  # b was not fetched again
            assert len(list((tmp_path / "media").iterdir())) == 2
        finally:
            await session.close()
            await server.close()

    asyncio.run(main())


def test_clear_cache_keeps_the_wall(tmp_path):
    async def main():
        host = Host({"a.jpg": 1 * KB, "b.jpg": 1 * KB})
        server, session, store, base, _ = await make_store(tmp_path, host, [10 ** 9])
        try:
            a, b = f"{base}/a.jpg", f"{base}/b.jpg"
            store.update([Want("A", [a], 0), Want("B", [b], 1)], False, [a], "s@1")
            await settle(store)
            store.clear(keep=[a])
            assert store.has(a) and not store.has(b)
            store.update([Want("A", [a], 0), Want("B", [b], 1)], False, [a], "s@1")
            await settle(store)
            assert store.has(b)  # fetched again as needed
        finally:
            await session.close()
            await server.close()

    asyncio.run(main())


def test_a_power_cut_leaves_nothing_inconsistent(tmp_path):
    media = tmp_path / "media"
    media.mkdir(parents=True)
    (tmp_path / "partial").mkdir()
    (tmp_path / "partial" / "half.part").write_bytes(b"half a file")
    (media / "orphan.jpg").write_bytes(b"no entry")
    (media / "good.jpg").write_bytes(b"12345")
    (tmp_path / "index.json").write_text(json.dumps({"files": {
        "http://h/good.jpg": {"url": "http://h/good.jpg", "name": "good.jpg", "bytes": 5, "mime": "image/jpeg", "last_used": 1},
        "http://h/gone.jpg": {"url": "http://h/gone.jpg", "name": "gone.jpg", "bytes": 5, "mime": "image/jpeg", "last_used": 1},
        "http://h/short.jpg": {"url": "http://h/short.jpg", "name": "good.jpg", "bytes": 99, "mime": "image/jpeg", "last_used": 1},
    }}))
    store = MediaStore(tmp_path, session=lambda: None, cap_bytes=lambda: 10 ** 9)
    store.load()
    assert list(store.files) == ["http://h/good.jpg"]
    assert sorted(p.name for p in media.iterdir()) == ["good.jpg"]
    assert list((tmp_path / "partial").iterdir()) == []


# Screen-sized copies ------------------------------------------------------------------------


def test_cover_size():
    assert cover_size(8000, 4000, 3840, 2160) == (4320, 2160, 0.54)


def test_large_originals_are_resized_to_cover_the_panel(tmp_path):
    from PIL import Image

    big = tmp_path / "big.part"
    Image.new("RGB", (6000, 4000), (200, 120, 40)).save(big, "JPEG")
    optimise = make_optimiser(lambda: (1920, 1080))
    out, mime, derived = optimise(big, "image/jpeg")
    with Image.open(out) as im:
        assert im.size == (1920, 1280) and mime == "image/jpeg" and derived == [1920, 1080]

    small = tmp_path / "small.part"
    Image.new("RGB", (2000, 1200), (1, 2, 3)).save(small, "PNG")
    assert optimise(small, "image/png") is None  # near panel size: kept exactly as it came

    gif = tmp_path / "anim.part"
    frames = [Image.new("P", (5000, 5000), i) for i in range(2)]
    frames[0].save(gif, "GIF", save_all=True, append_images=frames[1:])
    assert optimise(gif, "image/gif") is None  # animated: never touched

    rgba = tmp_path / "alpha.part"
    Image.new("RGBA", (8000, 8000), (0, 0, 0, 0)).save(rgba, "PNG")
    out, mime, _ = optimise(rgba, "image/png")
    assert mime == "image/webp"  # transparency kept
