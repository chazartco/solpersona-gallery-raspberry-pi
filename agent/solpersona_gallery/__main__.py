"""`python3 -m solpersona_gallery` — the agent service."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

import aiohttp
from aiohttp import web

from . import PLATFORM, __version__
from . import config as config_module
from .controller import Controller
from .display import Display, serial_suffix, watch_cec_remote
from .imageopt import make_optimiser
from .network import Network
from .sdnotify import notify
from .server import make_app
from .state_files import StateFiles
from .store import MediaStore

log = logging.getLogger("solpersona_gallery")


async def run(cfg: config_module.Config) -> None:
    for path in (cfg.state_dir, cfg.cache_dir, cfg.run_dir):
        path.mkdir(parents=True, exist_ok=True)

    session = aiohttp.ClientSession(
        headers={"User-Agent": f"SolPersonaGallery/{__version__} ({PLATFORM})"},
        timeout=aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=60),
    )
    display = Display(cfg.rotation, cfg.screen_off, cfg.manage_hardware)
    holder: dict = {}
    store = MediaStore(
        cfg.cache_dir,
        session=lambda: session,
        cap_bytes=lambda: holder["controller"].cap_bytes(),
        optimiser=make_optimiser(display.logical) if cfg.optimise_images else None,
    )
    store.load()
    network = Network(serial_suffix(), cfg.hotspot_password, cfg.manage_hardware, on_change=lambda: holder["controller"].changed())
    controller = Controller(cfg=cfg, files=StateFiles(cfg.state_dir), store=store, display=display, network=network, session=session)
    holder["controller"] = controller
    controller.on_tick = lambda: notify("WATCHDOG=1")

    runner = web.AppRunner(make_app(controller, cfg), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, cfg.bind, cfg.port).start()
    log.info(
        "SolPersona Gallery agent %s on %s:%d · %s · panel %s",
        __version__, cfg.bind, cfg.port, cfg.base_url, display.panel or "unknown",
    )

    controller.start()
    background = [asyncio.ensure_future(network.run_forever())]
    if cfg.manage_hardware:
        background.append(
            asyncio.ensure_future(
                watch_cec_remote(display.cec_device, lambda key: controller.event({"type": "key", "key": key}))
            )
        )
    notify("READY=1")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    notify("STOPPING=1")
    for task in background:
        task.cancel()
    await controller.stop()
    await runner.cleanup()
    await session.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    cfg = config_module.load()
    log.info("settings from %s", cfg.source)
    asyncio.run(run(cfg))


if __name__ == "__main__":
    main()
