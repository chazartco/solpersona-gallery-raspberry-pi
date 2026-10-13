"""systemd's notify protocol, without a dependency: READY once, then WATCHDOG
pets from the housekeeping loop — so a loop that hangs gets the agent killed
and restarted (`WatchdogSec=` in the unit)."""

from __future__ import annotations

import os
import socket


def notify(message: str) -> None:
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(message.encode("utf-8"))
    except OSError:
        pass
