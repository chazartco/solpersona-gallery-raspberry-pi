"""Small helpers shared by every module."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


def mono_ms() -> float:
    """Milliseconds on a clock that never jumps.

    The Pi has no battery-backed clock (the Pi 5's needs a cell fitted), so the
    wall clock is wrong until NTP answers and then jumps — often by months. The
    show's clock is therefore kept against the monotonic clock (SPEC §5.1).
    """
    return time.monotonic() * 1000.0


def wall_ms() -> float:
    return time.time() * 1000.0


def url_key(url: str) -> str:
    """A stable file name for a URL."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:40]


def fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Write-then-rename, fsynced, so a power cut leaves the old file or the new one — never half of either."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)  # umask must not loosen a secret file
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    fsync_dir(path.parent)


def atomic_write_json(path: Path, value: Any, mode: int = 0o600) -> None:
    atomic_write_bytes(path, json.dumps(value, separators=(",", ":")).encode("utf-8"), mode)


def read_json(path: Path, fallback: Any = None) -> Any:
    try:
        with open(path, "rb") as handle:
            return json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError):
        return fallback


def remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
