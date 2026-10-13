from solpersona_gallery.api import parse_retry_after
from solpersona_gallery.clock import ServerClock
from solpersona_gallery.config import load
from solpersona_gallery.network import (
    Network, default_route_device, parse_active_wifi, parse_devices, parse_reg_country, portal_html,
    should_open_hotspot, split_terse, wifi_qr_text,
)
from solpersona_gallery.rules import DEFAULT_SETTINGS, choose_candidates, normalise_settings


def test_settings_every_key_present_and_clamped():
    s = normalise_settings({"slideSeconds": 2, "washOpacity": 140, "prefetch": 0, "fit": "SPIN", "showYear": "yes", "mystery": 1})
    assert set(s) == set(DEFAULT_SETTINGS)
    assert s["slideSeconds"] == 5 and s["washOpacity"] == 100 and s["prefetch"] == 1
    assert s["fit"] == "BLUR" and s["showYear"] is True
    assert "mystery" not in s


def test_settings_schedule():
    assert normalise_settings({"schedule": {"on": "09:00", "off": "22:00"}})["schedule"] == {"on": "09:00", "off": "22:00"}
    assert normalise_settings({"schedule": {"on": "9", "off": "22:00"}})["schedule"] is None
    assert normalise_settings(None) == DEFAULT_SETTINGS


def test_candidates_order():
    item = {"kind": "IMAGE", "media": {"original": "O", "display": "D", "poster": "P", "fallbacks": ["F", "O"]}}
    assert choose_candidates(item, {"imageQuality": "AUTO"}, 3840, True) == ["O", "D", "F", "P"]
    assert choose_candidates(item, {"imageQuality": "AUTO"}, 1280, True) == ["D", "O", "F", "P"]
    video = {"kind": "VIDEO", "media": {"original": "V", "display": "D", "poster": "P", "fallbacks": ["V2"]}}
    assert choose_candidates(video, {"imageQuality": "DISPLAY"}, 3840, True) == ["V", "V2"]


def test_clock_median_and_seed():
    mono = [1000.0]
    clock = ServerClock(mono=lambda: mono[0], wall=lambda: 5000.0)
    for server, sent, received in [(10_100, 0, 200), (10_000, 0, 0), (99_999, 0, 0), (10_050, 0, 0), (10_020, 0, 0)]:
        clock.add(server, sent, received)
    assert clock.offset == 10_020  # one wild sample does not move it
    fresh = ServerClock(mono=lambda: 100.0, wall=lambda: 5000.0)
    fresh.seed(2500.0)
    assert fresh.now() == 7500.0  # wall + last known offset


def test_retry_after():
    assert parse_retry_after("7") == 7000
    assert parse_retry_after(None) is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", now=1445412480.0 - 10) == 10_000
    assert parse_retry_after("soon") is None


def test_config_rejects_bad_values(tmp_path, monkeypatch):
    path = tmp_path / "solpersona-gallery.toml"
    path.write_text('base_url = "http://192.168.1.20:8080/"\nrotation = 45\ncache_mb = -3\ndebug = true\nscreen_name = "Front window"\n')
    monkeypatch.setenv("SPG_CONFIG", str(path))
    cfg = load()
    assert cfg.base_url == "http://192.168.1.20:8080"
    assert cfg.rotation == 0 and cfg.cache_mb == 0
    assert cfg.debug is True and cfg.screen_name == "Front window"


def test_config_survives_a_broken_file(tmp_path, monkeypatch):
    path = tmp_path / "solpersona-gallery.toml"
    path.write_text("base_url = \n[[[")
    monkeypatch.setenv("SPG_CONFIG", str(path))
    assert load().base_url == "https://solpersona.com"


def test_hotspot_policy():
    base = dict(online=False, has_wifi_device=True, ethernet_carrier=False, offline_for_s=61, connected_this_boot=False, known_networks=1)
    assert should_open_hotspot(**base)
    assert not should_open_hotspot(**{**base, "offline_for_s": 30})
    assert not should_open_hotspot(**{**base, "ethernet_carrier": True})
    # An outage on a screen that was online this boot is not a setup problem…
    assert not should_open_hotspot(**{**base, "connected_this_boot": True})
    # …unless it knows no network at all.
    assert should_open_hotspot(**{**base, "connected_this_boot": True, "known_networks": 0})


def test_which_network_carries_the_traffic():
    devices = parse_devices("eth0:ethernet:connected:Wired connection 1\nwlan0:wifi:connected:Home\\:5G\nlo:loopback:unmanaged:\n")
    assert [d["device"] for d in devices] == ["eth0", "wlan0", "lo"] and devices[1]["connection"] == "Home:5G"
    routes = "default via 192.168.1.1 dev wlan0 proto dhcp src 192.168.1.40 metric 600\ndefault via 192.168.1.1 dev eth0 proto dhcp src 192.168.1.41 metric 100\n"
    assert default_route_device(routes) == "eth0"  # the cable wins: lower metric
    assert default_route_device("") is None


def test_wifi_signal_band_and_country():
    out = "no:Neighbours:40:2437 MHz\nyes:Home:72:5180 MHz\n"
    assert parse_active_wifi(out) == {"ssid": "Home", "signal": 72, "freqMhz": 5180, "band": "5 GHz"}
    assert parse_active_wifi("yes:Home:55:2412 MHz")["band"] == "2.4 GHz"
    assert parse_active_wifi("no:x:1:2412 MHz") is None
    assert parse_reg_country("global\ncountry 00: DFS-UNSET\n\t(2402 - 2472 @ 40), (6, 20)\n\nphy#0 (self-managed)\ncountry US: DFS-FCC\n") == "00"
    assert parse_reg_country("global\ncountry GB: DFS-ETSI\n") == "GB"


def test_the_country_chosen_on_the_screen_is_kept(tmp_path):
    net = Network("AB12", "", manage=False, on_change=lambda: None, country_file=tmp_path / "wifi-country")
    assert net.set_country("gb") and (tmp_path / "wifi-country").read_text().strip() == "GB"
    assert not net.set_country("Great Britain") and not net.set_country("")
    page = portal_html("SolPersona-Gallery-AB12", [], None, "GB")
    assert '<option value="GB" selected>United Kingdom</option>' in page


def test_wifi_qr_and_nmcli_parsing():
    assert wifi_qr_text("SolPersona-Gallery-AB12", "") == "WIFI:T:nopass;S:SolPersona-Gallery-AB12;;"
    assert wifi_qr_text("a;b", "p:w") == "WIFI:T:WPA;S:a\\;b;P:p\\:w;;"
    assert split_terse("Cafe\\: Upstairs:72:WPA2") == ["Cafe: Upstairs", "72", "WPA2"]
