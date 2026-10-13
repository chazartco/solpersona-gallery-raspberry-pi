"""The panel: its real size, and switching it off outside opening hours.

Size comes from KMS, which reads the panel's EDID: the first mode listed for a
connected HDMI connector is the panel's preferred (native) one. That is what
the screen reports as `screenWidth`/`screenHeight` — never Chromium's window,
which may be scaled.

Power (SPEC §8 `schedule`): outside opening hours the panel goes to standby
over HDMI-CEC; where CEC is absent or unanswered, the HDMI output is switched
off (most panels then sleep on "no signal"); and the player draws black in any
case, so an all-black screen is the last resort, never the only one.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

DRM = Path("/sys/class/drm")


def read_panel(drm: Path = DRM) -> Optional[dict]:
    """The connected HDMI connector and its preferred mode, or None."""
    connectors = sorted(drm.glob("card*-HDMI-A-*"))
    for connector in connectors:
        try:
            status = (connector / "status").read_text().strip()
            modes = (connector / "modes").read_text().split()
        except OSError:
            continue
        if status != "connected" or not modes:
            continue
        m = re.match(r"^(\d+)x(\d+)", modes[0])
        if not m:
            continue
        name = connector.name.split("-", 1)[1]  # card1-HDMI-A-1 → HDMI-A-1
        return {"connector": name, "width": int(m.group(1)), "height": int(m.group(2))}
    return None


def logical_size(panel: Optional[dict], rotation: int) -> Optional[tuple[int, int]]:
    """The panel as the art sees it: a screen hung in portrait is taller than wide."""
    if not panel:
        return None
    w, h = panel["width"], panel["height"]
    return (h, w) if rotation in (90, 270) else (w, h)


def memory_mb() -> int:
    try:
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def board_model() -> str:
    try:
        return Path("/proc/device-tree/model").read_text().strip("\x00\n ")
    except OSError:
        return ""


def serial_suffix() -> str:
    """Four characters that tell one Pi from another (for the setup network's name)."""
    for path in ("/sys/firmware/devicetree/base/serial-number", "/proc/device-tree/serial-number"):
        try:
            serial = Path(path).read_text().strip("\x00\n ")
            if serial:
                return serial[-4:].upper()
        except OSError:
            continue
    try:
        with open("/proc/cpuinfo") as handle:
            for line in handle:
                if line.lower().startswith("serial"):
                    return line.split(":")[1].strip()[-4:].upper()
    except (OSError, IndexError):
        pass
    return "0000"


async def run(*args: str, timeout: float = 10.0, env: Optional[dict] = None) -> tuple[int, str]:
    """A command's exit code and output; (127, '') when it is not installed."""
    if not shutil.which(args[0]):
        return 127, ""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        return proc.returncode or 0, out.decode("utf-8", "replace")
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except (ProcessLookupError, UnboundLocalError):
            pass
        return 124, ""
    except OSError:
        return 126, ""


class Display:
    def __init__(self, rotation: int, mode: str = "auto", manage: bool = True, drm: Path = DRM) -> None:
        self.rotation = rotation
        self.mode = mode  # auto | cec | hdmi | black
        self.manage = manage
        self.drm = drm
        self.panel: Optional[dict] = None
        self.powered: Optional[bool] = None
        self.method = "none"  # how the panel was last switched: cec | hdmi | black
        self.cec_device = self._cec_device()
        self._cec_ready = False
        self.refresh()

    def refresh(self) -> Optional[dict]:
        if self.manage:
            self.panel = read_panel(self.drm)
        return self.panel

    def logical(self) -> Optional[tuple[int, int]]:
        return logical_size(self.panel, self.rotation)

    def can_hold_originals(self) -> bool:
        # Originals are resized to the panel before Chromium sees them, so any
        # Pi with 2 GB or more can show them; a 1 GB Pi 4 takes the display copies.
        mb = memory_mb()
        return mb == 0 or mb >= 1800

    # Power -----------------------------------------------------------------------

    def _cec_device(self) -> Optional[str]:
        devices = sorted(glob.glob("/dev/cec*"))
        return devices[0] if devices else None

    async def _cec(self, *args: str) -> bool:
        if not self.cec_device:
            return False
        if not self._cec_ready:
            # Claim a logical address as a playback device, once.
            code, _ = await run("cec-ctl", "-d", self.cec_device, "--playback", "--osd-name", "SolPersona")
            self._cec_ready = code == 0
            if not self._cec_ready:
                return False
        code, out = await run("cec-ctl", "-d", self.cec_device, "--to", "0", *args)
        # cec-ctl reports a message nobody acknowledged in its output, not always in its exit code.
        return code == 0 and "Not Acknowledged" not in out and "NACK" not in out

    async def _hdmi(self, on: bool) -> bool:
        if not self.panel:
            return False
        env = dict(os.environ)
        runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        env["XDG_RUNTIME_DIR"] = runtime
        env.setdefault("WAYLAND_DISPLAY", "wayland-0")
        code, _ = await run("wlr-randr", "--output", self.panel["connector"], "--on" if on else "--off", env=env)
        return code == 0

    async def set_power(self, on: bool) -> str:
        """Switch the panel. Returns how: 'cec', 'hdmi' or 'black' (the player's black alone)."""
        if self.powered is on:
            return self.method
        self.powered = on
        if not self.manage or self.mode == "black":
            self.method = "black"
            return self.method
        method = "black"
        if self.mode in ("auto", "cec"):
            ok = await (self._cec("--image-view-on") if on else self._cec("--standby"))
            if ok:
                method = "cec"
                if on:
                    await self._cec("--active-source", "phys-addr=" + await self._phys_addr())
        if method == "black" and self.mode in ("auto", "hdmi"):
            # Turning HDMI back on is always tried: the output may have been switched off by us.
            if await self._hdmi(on):
                method = "hdmi"
        elif on and self.method == "hdmi":
            await self._hdmi(True)
        self.method = method
        log.info("panel %s (%s)", "on" if on else "off", method)
        return method

    async def _phys_addr(self) -> str:
        code, out = await run("cec-ctl", "-d", self.cec_device or "/dev/cec0")
        m = re.search(r"Physical Address\s*:\s*([0-9a-f.]+)", out)
        return m.group(1) if m else "1.0.0.0"


# Remote control over HDMI-CEC ----------------------------------------------------------

CEC_KEYS = {
    "select": "Enter",
    "ok": "Enter",
    "exit": "Escape",
    "back": "Escape",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "left": "ArrowLeft",
    "right": "ArrowRight",
}


def parse_cec_key(line: str) -> Optional[str]:
    """A key from a `cec-ctl --monitor` line such as '\tui-cmd: select (0x00)'."""
    m = re.search(r"ui-cmd:\s*([a-z0-9-]+)", line)
    return CEC_KEYS.get(m.group(1)) if m else None


async def watch_cec_remote(device: Optional[str], on_key: Callable[[str], None]) -> None:
    """Forward the TV remote's OK/Back/arrows to the player, while cec-ctl is there to listen."""
    if not device or not shutil.which("cec-ctl"):
        return
    while True:
        try:
            proc = await asyncio.create_subprocess_exec(
                "cec-ctl", "-d", device, "--monitor", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
            )
            assert proc.stdout is not None
            pressed = False
            async for raw in proc.stdout:
                line = raw.decode("utf-8", "replace")
                if "USER_CONTROL_PRESSED" in line:
                    pressed = True
                    continue
                if pressed:
                    pressed = False
                    key = parse_cec_key(line)
                    if key:
                        on_key(key)
            await proc.wait()
        except asyncio.CancelledError:
            raise
        except OSError:
            return
        await asyncio.sleep(10)
