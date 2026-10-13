"""What the agent keeps across restarts, in /var/lib/solpersona-gallery.

That directory is on the card's data partition — the only writable part of
the disk besides the media cache, so a power cut can never corrupt the system.
Every file is written whole (write, fsync, rename) and readable only by the
`gallery` user (mode 0600).

* `device.json`  — the credential: { baseUrl, deviceId, token, ackSeq }. The
  access token is in this file and nowhere else: never in the browser, never in
  a log.
* `pairing.json` — an unexpired registration, so a restart keeps showing the
  same code (it holds the short-lived deviceCode).
* `show.json`    — the last playlist and clock, so a reboot with no network
  still plays.
* `plays.json`   — time played while offline (SPEC §4.1), until a sync takes it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from .util import atomic_write_json, read_json, remove

log = logging.getLogger(__name__)


class StateFiles:
    def __init__(self, state_dir: Path) -> None:
        self.dir = state_dir
        self.device_path = state_dir / "device.json"
        self.pairing_path = state_dir / "pairing.json"
        self.show_path = state_dir / "show.json"
        self.plays_path = state_dir / "plays.json"

    # The credential -------------------------------------------------------

    def load_device(self, base_url: str) -> Optional[dict]:
        device = read_json(self.device_path)
        if not isinstance(device, dict) or not device.get("token"):
            return None
        if device.get("baseUrl") != base_url:
            # A credential belongs to the SolPersona that issued it.
            log.info("stored screen belongs to another SolPersona; pairing again")
            return None
        return device

    def save_device(self, device: dict) -> None:
        atomic_write_json(self.device_path, device)

    def clear_device(self) -> None:
        remove(self.device_path)

    # Pairing ----------------------------------------------------------------

    def load_pairing(self) -> Optional[dict]:
        value = read_json(self.pairing_path)
        return value if isinstance(value, dict) else None

    def save_pairing(self, registration: Optional[dict]) -> None:
        if registration is None:
            remove(self.pairing_path)
        else:
            atomic_write_json(self.pairing_path, registration)

    # The playlist -----------------------------------------------------------

    def load_show(self) -> Optional[dict]:
        value = read_json(self.show_path)
        return value if isinstance(value, dict) else None

    def save_show(self, snapshot: Optional[dict]) -> None:
        if snapshot is None:
            remove(self.show_path)
        else:
            atomic_write_json(self.show_path, snapshot, mode=0o600)

    # Offline plays ------------------------------------------------------------

    def load_plays(self) -> list[dict]:
        value = read_json(self.plays_path, [])
        return [p for p in value if isinstance(p, dict)] if isinstance(value, list) else []

    def save_plays(self, plays: list[dict]) -> None:
        if not plays:
            remove(self.plays_path)
        else:
            atomic_write_json(self.plays_path, plays)

    def forget_all(self) -> None:
        for path in (self.device_path, self.pairing_path, self.show_path, self.plays_path):
            remove(path)

    def load(self, name: str) -> Any:
        return read_json(self.dir / name)
