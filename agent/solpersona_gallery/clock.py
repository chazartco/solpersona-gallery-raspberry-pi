"""Server time (SPEC §5.1), kept against the monotonic clock."""

from __future__ import annotations

from collections import deque
from typing import Callable, Optional

from .util import mono_ms, wall_ms


def offset_sample(server_time: float, sent_at: float, received_at: float) -> float:
    """One estimate of (server − local): server time minus the midpoint of the round trip."""
    return server_time - (sent_at + received_at) / 2.0


class ServerClock:
    """`now()` is the server's time in ms.

    Samples are taken against the monotonic clock, so an NTP step on the Pi's
    wall clock (it has no RTC, so the first one can be months) moves nothing.
    The offset is the median of the last five samples, so one slow poll cannot
    throw the screen off — the same choice the reference makes.
    """

    def __init__(self, mono: Callable[[], float] = mono_ms, wall: Callable[[], float] = wall_ms) -> None:
        self._mono = mono
        self._wall = wall
        self.samples: deque[float] = deque(maxlen=5)
        self.offset = wall() - mono()  # until the first sync: trust the wall clock
        self.rtt: Optional[float] = None
        self.synced = False

    def add(self, server_time: object, sent_at: float, received_at: float) -> None:
        if not isinstance(server_time, (int, float)) or isinstance(server_time, bool):
            return
        self.samples.append(offset_sample(float(server_time), sent_at, received_at))
        ordered = sorted(self.samples)
        self.offset = ordered[len(ordered) // 2]
        self.rtt = received_at - sent_at
        self.synced = True

    def now(self) -> float:
        return self._mono() + self.offset

    def wall_offset(self) -> float:
        """Server time minus the wall clock — what survives a reboot (the monotonic clock does not)."""
        return self.now() - self._wall()

    def seed(self, wall_offset: Optional[float]) -> None:
        """After a reboot with no network: carry on from the last known offset."""
        if self.synced or not isinstance(wall_offset, (int, float)):
            return
        self.offset = self._wall() + float(wall_offset) - self._mono()
