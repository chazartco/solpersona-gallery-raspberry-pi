"""Settings a person may need, from `/boot/firmware/solpersona-gallery.toml`.

The file sits on the FAT boot partition so anybody can edit it from any
computer that mounts the card. A mistake in it must never stop the screen:
every bad value is logged and replaced by its default.

Paths can be moved with environment variables, for development on a laptop:
SPG_CONFIG, SPG_STATE_DIR, SPG_CACHE_DIR, SPG_RUN_DIR, SPG_PLAYER_DIR, SPG_PORT.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib  # Python 3.11+, as on Bookworm
except ModuleNotFoundError:  # pragma: no cover - older dev machines
    tomllib = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

DEFAULT_CONFIG = Path("/boot/firmware/solpersona-gallery.toml")
DEFAULT_BASE_URL = "https://solpersona.com"


@dataclass
class Config:
    base_url: str = DEFAULT_BASE_URL
    screen_name: str = ""
    cache_mb: int = 0  # 0: automatic, from the card's free space
    debug: bool = False
    rotation: int = 0
    timezone: str = ""
    wifi_country: str = ""
    resolution: str = "auto"  # auto | 1080p | 4k
    screen_off: str = "auto"  # auto | cec | hdmi | black
    hotspot_password: str = ""
    updates: bool = True
    os_updates: str = "weekly"  # weekly | off
    optimise_images: bool = True

    # Where things live. Not in the TOML: these are the image's layout.
    state_dir: Path = Path("/var/lib/solpersona-gallery")
    cache_dir: Path = Path("/var/cache/solpersona-gallery")
    run_dir: Path = Path("/run/solpersona-gallery")
    player_dir: Path = Path("/usr/lib/solpersona-gallery/player")
    port: int = 8737
    bind: str = "127.0.0.1"  # never anything else on a Pi; SPG_BIND exists for containers in development
    manage_hardware: bool = True  # CEC, HDMI, KMS, NetworkManager; off on a laptop
    dev_origins: list[str] = field(default_factory=list)
    source: str = "defaults"


def normalise_base(url: str) -> str:
    return str(url or "").strip().rstrip("/")


def _valid_base(url: str) -> bool:
    return bool(re.match(r"^https?://[^/\s]+(:\d+)?$", url))


def load(path: Path | None = None) -> Config:
    cfg = Config()
    env = os.environ
    path = Path(env.get("SPG_CONFIG", str(path or DEFAULT_CONFIG)))
    raw: dict = {}
    if path.exists():
        if tomllib is None:
            log.warning("cannot read %s: no TOML parser in this Python", path)
        else:
            try:
                with open(path, "rb") as handle:
                    raw = tomllib.load(handle)
                cfg.source = str(path)
            except (OSError, ValueError) as error:
                log.warning("ignoring %s: %s", path, error)

    def take(key: str, kind: type, check=None):
        if key not in raw:
            return
        value = raw[key]
        if kind is int and isinstance(value, bool):
            ok = False
        else:
            ok = isinstance(value, kind) and (check is None or check(value))
        if ok:
            setattr(cfg, key, value)
        else:
            log.warning("%s: ignoring %s = %r", path, key, value)

    take("base_url", str, lambda v: _valid_base(normalise_base(v)))
    take("screen_name", str, lambda v: len(v) <= 60)
    take("cache_mb", int, lambda v: v >= 0)
    take("debug", bool)
    take("rotation", int, lambda v: v in (0, 90, 180, 270))
    take("timezone", str, lambda v: v == "" or re.match(r"^[A-Za-z0-9_+\-/]+$", v) is not None)
    take("wifi_country", str, lambda v: v == "" or re.match(r"^[A-Z]{2}$", v) is not None)
    take("resolution", str, lambda v: v in ("auto", "1080p", "4k"))
    take("screen_off", str, lambda v: v in ("auto", "cec", "hdmi", "black"))
    take("hotspot_password", str, lambda v: v == "" or 8 <= len(v) <= 63)
    take("updates", bool)
    take("os_updates", str, lambda v: v in ("weekly", "off"))
    take("optimise_images", bool)
    cfg.base_url = normalise_base(cfg.base_url)

    if "SPG_STATE_DIR" in env:
        cfg.state_dir = Path(env["SPG_STATE_DIR"])
    if "SPG_CACHE_DIR" in env:
        cfg.cache_dir = Path(env["SPG_CACHE_DIR"])
    if "SPG_RUN_DIR" in env:
        cfg.run_dir = Path(env["SPG_RUN_DIR"])
    if "SPG_PLAYER_DIR" in env:
        cfg.player_dir = Path(env["SPG_PLAYER_DIR"])
    if "SPG_PORT" in env and env["SPG_PORT"].isdigit():
        cfg.port = int(env["SPG_PORT"])
    if "SPG_BASE_URL" in env and _valid_base(normalise_base(env["SPG_BASE_URL"])):
        cfg.base_url = normalise_base(env["SPG_BASE_URL"])
    if env.get("SPG_DEV") == "1":
        cfg.manage_hardware = False
        cfg.debug = env.get("SPG_DEBUG", "1") == "1"
        cfg.bind = env.get("SPG_BIND", cfg.bind)
        if "base_url" not in raw and "SPG_BASE_URL" not in env:
            # A development run never talks to production by accident.
            cfg.base_url = "http://localhost:8080"
    cfg.dev_origins = [o for o in env.get("SPG_DEV_ORIGINS", "").split(",") if o]
    return cfg


def shell_exports(cfg: Config) -> str:
    """The settings the kiosk's shell script needs, as `KEY=value` lines."""
    values = {
        "SPG_ROTATION": cfg.rotation,
        "SPG_RESOLUTION": cfg.resolution,
        "SPG_DEBUG": "1" if cfg.debug else "0",
        "SPG_PORT": cfg.port,
        "SPG_TIMEZONE": cfg.timezone,
        "SPG_WIFI_COUNTRY": cfg.wifi_country,
        "SPG_UPDATES": "1" if cfg.updates else "0",
        "SPG_OS_UPDATES": cfg.os_updates,
    }
    return "".join(f"{key}={shlex.quote(str(value))}\n" for key, value in values.items())


if __name__ == "__main__":  # `python3 -m solpersona_gallery.config --shell`
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(message)s")
    if "--shell" in sys.argv:
        sys.stdout.write(shell_exports(load()))
    else:
        print(load())
