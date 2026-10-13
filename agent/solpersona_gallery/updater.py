"""Updates: the app from signed GitHub releases, the OS from Debian security.

Run as root by `solpersona-updater.timer` every few minutes; almost every run
does nothing. Always run from the copy on the read-only root
(/usr/lib/solpersona-gallery), never from an updated copy, so a bad release
cannot break its own way back.

The app (player + agent)
  * Checked once a day against the repository's latest GitHub release.
  * A release is trusted only if SHA256SUMS carries a valid Ed25519 signature
    by the key baked into the image, and the bundle matches its sum.
  * Unpacked beside the running version in /var/lib/solpersona-gallery-app/,
    then switched to at the next dark window (outside opening hours) or 03:00
    local — and only while the running version is syncing fine, so a new
    version that cannot sync is the new version's fault.
  * If the new version has not reported a successful sync within five minutes,
    the previous one is switched back and the release is never tried again.

The OS (security updates only, weekly)
  The root file system is a read-only overlay, so updates applied in place
  would vanish at the next boot. In a dark window the overlay is switched off
  and the Pi reboots; `os-boot` (at boot, before the kiosk) applies
  `unattended-upgrade` — configured for the Debian security origin only —
  switches the overlay back on and reboots again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Optional

log = logging.getLogger("solpersona_gallery.updater")

FACTORY = Path("/usr/lib/solpersona-gallery")
APP_ROOT = Path("/var/lib/solpersona-gallery-app")
STATUS = Path("/run/solpersona-gallery/status.json")
SERVICES = ["solpersona-agent.service", "solpersona-kiosk.service"]
CHECK_EVERY_S = 24 * 3600
VERIFY_AFTER_S = 5 * 60
SYNC_FRESH_MS = 10 * 60 * 1000
OS_EVERY_S = 7 * 24 * 3600
BUNDLE = "solpersona-gallery-app-{version}.tar.gz"


# Pure decisions (tested) ---------------------------------------------------------------


def parse_version(text: str) -> Optional[tuple[int, int, int]]:
    parts = text.strip().lstrip("v").split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    return int(parts[0]), int(parts[1]), int(parts[2])


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


def in_window(status: dict, local_hour: int) -> bool:
    """Outside opening hours (the screen is dark anyway), or 3 a.m."""
    return bool(status.get("dark")) or local_hour == 3


def syncing_fine(status: dict, now_ms: float) -> bool:
    last = status.get("lastSyncOkAt")
    return isinstance(last, (int, float)) and now_ms - last < SYNC_FRESH_MS


def verdict(pending: dict, status: dict, now_s: float) -> str:
    """For a version switched to at pending['switchedAt']: keep | wait | rollback."""
    last = status.get("lastSyncOkAt")
    synced = status.get("appVersion") == pending["version"] and isinstance(last, (int, float)) and last / 1000 > pending["switchedAt"]
    if synced:
        return "keep"
    return "rollback" if now_s - pending["switchedAt"] >= VERIFY_AFTER_S else "wait"


def safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Only plain files and directories, only inside the bundle (Python 3.11.2 has no extraction filter)."""
    out = []
    for member in tar.getmembers():
        name = member.name
        if name.startswith("/") or ".." in Path(name).parts or not (member.isfile() or member.isdir()):
            raise ValueError(f"unsafe entry in bundle: {name!r}")
        out.append(member)
    return out


def verify_signature(sums: Path, signature: Path, public_key: Path) -> bool:
    """Ed25519 over SHA256SUMS, checked with the image's OpenSSL."""
    result = subprocess.run(
        ["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(public_key), "-rawin", "-in", str(sums), "-sigfile", str(signature)],
        capture_output=True,
    )
    return result.returncode == 0 and b"Signature Verified Successfully" in result.stdout


def sum_for(sums_text: str, name: str) -> Optional[str]:
    for line in sums_text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            return parts[0].lower()
    return None


# State --------------------------------------------------------------------------------------


class Updater:
    def __init__(self, factory: Path = FACTORY, root: Path = APP_ROOT, status_path: Path = STATUS) -> None:
        self.factory = factory
        self.root = root
        self.status_path = status_path
        self.state_path = root / "updates.json"
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.state = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            self.state = {}
        self.state.setdefault("bad", [])

    def save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1))
        os.replace(tmp, self.state_path)

    def status(self) -> dict:
        try:
            return json.loads(self.status_path.read_text())
        except (OSError, ValueError):
            return {}

    def current_version(self) -> str:
        for base in (self.root / "current", self.factory):
            try:
                return (base / "VERSION").read_text().strip()
            except OSError:
                continue
        return "0.0.0"

    def repo(self) -> Optional[str]:
        try:
            repo = (self.factory / "release-repo").read_text().strip()
        except OSError:
            return None
        return repo if repo.count("/") == 1 else None

    def public_key(self) -> Optional[Path]:
        key = self.factory / "release-signing.pub"
        try:
            return key if "BEGIN PUBLIC KEY" in key.read_text() else None
        except OSError:
            return None

    def updates_enabled(self) -> bool:
        try:
            out = subprocess.run([sys.executable, "-m", "solpersona_gallery.config", "--shell"], capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            return True
        return "SPG_UPDATES=0" not in out

    # Checking and staging ------------------------------------------------------------------

    def check(self, force: bool = False) -> Optional[str]:
        """Fetch, verify and unpack a newer release. Returns its version when one is staged."""
        now = time.time()
        if not force and now - self.state.get("lastCheck", 0) < CHECK_EVERY_S:
            return self.state.get("staged")
        repo, key = self.repo(), self.public_key()
        if not repo or not key:
            log.info("updates are off in this build (no release repository or signing key)")
            self.state["lastCheck"] = now
            self.save()
            return None
        self.state["lastCheck"] = now
        self.save()
        release = self._get_json(f"https://api.github.com/repos/{repo}/releases/latest")
        tag = str(release.get("tag_name") or "")
        version = tag.lstrip("v")
        if not is_newer(version, self.current_version()) or version in self.state["bad"]:
            return None
        if (self.root / version / "VERSION").is_file():
            self.state["staged"] = version
            self.save()
            return version
        assets = {a.get("name"): a.get("browser_download_url") for a in release.get("assets") or []}
        bundle = BUNDLE.format(version=version)
        if not all(assets.get(n) for n in (bundle, "SHA256SUMS", "SHA256SUMS.sig")):
            log.warning("release %s is missing its bundle or signature", tag)
            return None
        with tempfile.TemporaryDirectory(dir=self.root) as tmp:
            work = Path(tmp)
            for name in (bundle, "SHA256SUMS", "SHA256SUMS.sig"):
                self._download(assets[name], work / name)
            if not verify_signature(work / "SHA256SUMS", work / "SHA256SUMS.sig", key):
                log.error("release %s: bad signature — ignored", tag)
                self.state["bad"].append(version)
                self.save()
                return None
            expected = sum_for((work / "SHA256SUMS").read_text(), bundle)
            actual = hashlib.sha256((work / bundle).read_bytes()).hexdigest()
            if expected != actual:
                log.error("release %s: bundle does not match its signed sum — ignored", tag)
                return None
            target = work / "unpacked"
            with tarfile.open(work / bundle, "r:gz") as tar:
                tar.extractall(target, members=safe_members(tar))
            if (target / "VERSION").read_text().strip() != version:
                log.error("release %s: bundle says it is another version — ignored", tag)
                return None
            os.replace(target, self.root / version)
        self.state["staged"] = version
        self.save()
        log.info("version %s is ready; it goes live at the next dark window or 03:00", version)
        return version

    def _get_json(self, url: str) -> dict:
        request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "solpersona-gallery-updater"})
        with urllib.request.urlopen(request, timeout=30) as res:
            return json.loads(res.read().decode("utf-8"))

    def _download(self, url: str, path: Path) -> None:
        request = urllib.request.Request(url, headers={"User-Agent": "solpersona-gallery-updater"})
        with urllib.request.urlopen(request, timeout=120) as res, open(path, "wb") as out:
            shutil.copyfileobj(res, out)

    # Switching ---------------------------------------------------------------------------------

    def _point(self, link: str, version: Optional[str]) -> None:
        path = self.root / link
        if version is None:
            if path.is_symlink():
                path.unlink()
            return
        tmp = self.root / f".{link}.tmp"
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        tmp.symlink_to(version)
        os.replace(tmp, path)

    def _linked(self, link: str) -> Optional[str]:
        path = self.root / link
        return os.readlink(path) if path.is_symlink() else None

    def apply(self, version: str) -> None:
        self.state["pending"] = {"version": version, "switchedAt": time.time(), "previous": self._linked("current")}
        self.save()
        self._point("current", version)
        log.info("switched to %s; it has five minutes to sync", version)
        restart()

    def rollback(self) -> None:
        pending = self.state.pop("pending", None)
        if pending:
            self.state["bad"].append(pending["version"])
            self._point("current", pending.get("previous"))
        self.state.pop("staged", None)
        self.save()
        log.warning("rolled back from %s", pending["version"] if pending else "?")
        restart()

    def commit(self) -> None:
        pending = self.state.pop("pending")
        self.state.pop("staged", None)
        if pending.get("previous"):
            self._point("previous", pending["previous"])
        keep = {pending["version"], pending.get("previous")}
        for path in self.root.iterdir():
            if path.is_dir() and not path.is_symlink() and parse_version(path.name) and path.name not in keep:
                shutil.rmtree(path, ignore_errors=True)
        self.save()
        log.info("version %s is good", pending["version"])

    # The timer's run -------------------------------------------------------------------------------

    def tick(self) -> None:
        status = self.status()
        pending = self.state.get("pending")
        if pending:
            outcome = verdict(pending, status, time.time())
            if outcome == "keep":
                self.commit()
            elif outcome == "rollback":
                self.rollback()
            return
        if not self.updates_enabled():
            return
        try:
            staged = self.check()
        except (OSError, ValueError) as error:
            log.info("update check failed: %s", error)
            staged = self.state.get("staged")
        hour = time.localtime().tm_hour
        if staged and in_window(status, hour) and syncing_fine(status, time.time() * 1000):
            self.apply(staged)
            return
        self.os_maintenance_if_due(status, hour)

    # OS security updates ----------------------------------------------------------------------------

    def os_maintenance_if_due(self, status: dict, hour: int) -> None:
        if os_updates_setting() == "off":
            return
        if time.time() - self.state.get("lastOs", 0) < OS_EVERY_S or not in_window(status, hour):
            return
        if not overlay_active():
            # Not read-only (a development install): update in place.
            run_security_updates()
            self.state["lastOs"] = time.time()
            self.save()
            return
        (self.root / "os-maintenance").write_text(str(time.time()))
        self.state["lastOs"] = time.time()
        self.save()
        log.info("switching the overlay off for security updates; rebooting")
        subprocess.run([str(self.factory / "overlay.sh"), "off"], capture_output=True)
        subprocess.run(["systemctl", "reboot"])

    def os_boot(self) -> None:
        """At boot, before the kiosk: finish a maintenance reboot."""
        marker = self.root / "os-maintenance"
        if not marker.exists():
            return
        marker.unlink()
        if overlay_active():
            log.warning("the overlay did not switch off; skipping OS updates this week")
            return
        try:
            run_security_updates()
        finally:
            # Whatever happened, the card goes back to read-only.
            log.info("switching the overlay back on; rebooting")
            subprocess.run([str(self.factory / "overlay.sh"), "on"], capture_output=True)
            subprocess.run(["systemctl", "reboot"])


def overlay_active() -> bool:
    try:
        out = subprocess.run(["findmnt", "-n", "-o", "FSTYPE", "/"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return False
    return out == "overlay"


def run_security_updates() -> None:
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    subprocess.run(["apt-get", "update", "-q"], env=env, timeout=1800)
    subprocess.run(["unattended-upgrade", "-v"], env=env, timeout=3600)


def os_updates_setting() -> str:
    try:
        out = subprocess.run([sys.executable, "-m", "solpersona_gallery.config", "--shell"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return "weekly"
    return "off" if "SPG_OS_UPDATES=off" in out else "weekly"


def restart() -> None:
    subprocess.run(["systemctl", "restart", *SERVICES])


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="solpersona-gallery-update")
    parser.add_argument("command", choices=["tick", "check", "rollback", "status", "os-boot"])
    args = parser.parse_args()
    updater = Updater()
    if args.command == "tick":
        updater.tick()
    elif args.command == "check":
        print(updater.check(force=True) or "up to date")
    elif args.command == "rollback":
        updater.rollback()
    elif args.command == "status":
        print(json.dumps({"current": updater.current_version(), **updater.state, "agent": updater.status()}, indent=1))
    elif args.command == "os-boot":
        updater.os_boot()


if __name__ == "__main__":
    main()
