import io
import subprocess
import tarfile

import pytest

from solpersona_gallery.updater import Updater, in_window, is_newer, safe_members, sum_for, syncing_fine, verdict, verify_signature


def test_versions():
    assert is_newer("1.2.10", "1.2.9") and is_newer("v2.0.0", "1.9.9")
    assert not is_newer("1.2.3", "1.2.3") and not is_newer("1.3.0-rc1", "1.2.0") and not is_newer("garbage", "1.0.0")


def test_window_and_health():
    assert in_window({"dark": True}, 14) and in_window({}, 3) and not in_window({"dark": False}, 14)
    assert syncing_fine({"lastSyncOkAt": 1_000_000}, 1_000_000 + 60_000)
    assert not syncing_fine({"lastSyncOkAt": None}, 0)


def test_rollback_if_no_sync_within_five_minutes():
    pending = {"version": "1.1.0", "switchedAt": 1000.0}
    synced = {"appVersion": "1.1.0", "lastSyncOkAt": 1010_000}
    assert verdict(pending, synced, 1020) == "keep"
    assert verdict(pending, {"appVersion": "1.1.0", "lastSyncOkAt": 990_000}, 1100) == "wait"
    assert verdict(pending, {"appVersion": "1.1.0", "lastSyncOkAt": 990_000}, 1301) == "rollback"
    assert verdict(pending, {"appVersion": "1.0.0", "lastSyncOkAt": 1200_000}, 1301) == "rollback"  # the old one is running


def test_bundles_cannot_escape():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("../../etc/cron.d/evil")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar, pytest.raises(ValueError):
        safe_members(tar)


def test_signature_check(tmp_path):
    key, pub = tmp_path / "key.pem", tmp_path / "pub.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(key)], check=True)
    subprocess.run(["openssl", "pkey", "-in", str(key), "-pubout", "-out", str(pub)], check=True)
    sums = tmp_path / "SHA256SUMS"
    sums.write_text("ab" * 32 + "  solpersona-gallery-app-1.0.0.tar.gz\n")
    sig = tmp_path / "SHA256SUMS.sig"
    subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(key), "-rawin", "-in", str(sums), "-out", str(sig)], check=True)
    assert verify_signature(sums, sig, pub)
    sums.write_text("cd" * 32 + "  solpersona-gallery-app-1.0.0.tar.gz\n")  # tampered
    assert not verify_signature(sums, sig, pub)
    assert sum_for("ab" * 32 + "  solpersona-gallery-app-1.0.0.tar.gz", "solpersona-gallery-app-1.0.0.tar.gz") == "ab" * 32


def test_switch_and_roll_back(tmp_path, monkeypatch):
    monkeypatch.setattr("solpersona_gallery.updater.restart", lambda: None)
    factory, root = tmp_path / "factory", tmp_path / "app"
    factory.mkdir()
    (factory / "VERSION").write_text("1.0.0")
    for v in ("1.1.0", "1.2.0"):
        (root / v).mkdir(parents=True)
        (root / v / "VERSION").write_text(v)
    up = Updater(factory=factory, root=root, status_path=tmp_path / "status.json")
    assert up.current_version() == "1.0.0"
    up.apply("1.1.0")
    assert up.current_version() == "1.1.0"
    up.commit()
    up.apply("1.2.0")
    up.rollback()
    assert up.current_version() == "1.1.0" and "1.2.0" in up.state["bad"]
