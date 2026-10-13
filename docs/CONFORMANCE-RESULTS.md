# Conformance results

Against [`solpersona/CONFORMANCE.md`](solpersona/CONFORMANCE.md).

> **Status: not yet run on a Raspberry Pi.** Nothing below has been measured on
> a Pi 5 or a Pi 4. The image has not yet been built and booted. The *Dev*
> column is what was checked on 12 October 2026 with the real agent (in Debian
> Bookworm, Docker) and the real player (in desktop Edge/Chromium), against the
> mock device API in `dev/mock_solpersona.py`. That proves the logic, not the
> hardware. Fill in the Pi columns from a real run before the first release.

Legend: ✅ passed · ❌ failed · ⏳ not yet run · — not applicable

## A. Automated (CI, every push)

| | Dev | CI |
|---|---|---|
| Clock passes every case in `test-vectors.json` (`positionAt`, `clockOffset`, `imageChoice`, `schedule`) — TypeScript port (`player/test/vectors.test.ts`) **and** Python port (`agent/tests/test_vectors.py`) | ✅ | ⏳ first run on GitHub |
| Sync handling: new version vs same version | ✅ `test_new_version_replaces_the_playlist_same_version_only_moves_the_clock` | ⏳ |
| Commands de-duplicated by `seq` | ✅ `test_commands_run_once_and_are_acknowledged` | ⏳ |
| `401 SCREEN_NOT_LINKED` → back to pairing | ✅ `test_removed_from_the_dashboard_goes_back_to_pairing` | ⏳ |
| `429` honours `Retry-After` (register and sync) | ✅ `test_register_honours_retry_after`, `test_rate_limited_sync_waits_retry_after` | ⏳ |
| Store: LRU, size cap, keepOffline, new version, power cut | ✅ `agent/tests/test_store.py` | ⏳ |

## B. Pairing

| | Dev | Pi 5 | Pi 4 |
|---|---|---|---|
| First boot goes straight to pairing: QR, code, countdown, legible from 3 m | ✅ screen correct at 1080p and in portrait; 3 m legibility ⏳ | ⏳ | ⏳ |
| Linking from a phone → playing within ~3 s | ✅ (mock link → playing in < 3 s) | ⏳ | ⏳ |
| Unlinked 15+ min → a fresh code by itself | ✅ unit test (expired code → new code) | ⏳ | ⏳ |
| Power-cycle after linking → back linked and playing, no code | ✅ agent restart keeps the credential | ⏳ | ⏳ |
| Removed from the dashboard → pairing within one poll | ✅ | ⏳ | ⏳ |
| "Forget this account" from the device's menu | ✅ unit test; overlay screen ✅ | ⏳ | ⏳ |
| Try a sample gallery: plays, code still in a corner, linking switches over, Back returns | ✅ | ⏳ | ⏳ |
| Every text matches SPEC §11c | ✅ by reading; unlicensed screen ✅ | ⏳ | ⏳ |

## C2. Storage modes

| | Dev | Pi 5 | Pi 4 |
|---|---|---|---|
| Reports `persistentStorage: true` and stores every piece in order; dashboard shows n of N | ✅ (`stored 6 of 6`) | ⏳ | ⏳ |
| Network pulled + power-cycled: plays the whole show from storage | ✅ (agent restarted with the API unreachable: played from the card, QR codes included) | ⏳ | ⏳ |
| Gallery changed: new pieces downloaded, unused ones deleted | ✅ unit test | ⏳ | ⏳ |
| Option off: stored copy cleared | — see `QUESTIONS.md` §1 | ⏳ | ⏳ |

## C. Playback

| | Dev | Pi 5 | Pi 4 |
|---|---|---|---|
| Pieces change every `slideSeconds` with the chosen transition | ✅ FADE; DISSOLVE, CUT ⏳ | ⏳ | ⏳ |
| Pause / resume / next / previous / jump / restart within one poll | ✅ next (unit test), jump (on screen); others ⏳ | ⏳ | ⏳ |
| Two devices in step within ~150 ms over 10 min | ⏳ | ⏳ | ⏳ |
| Video plays to its end, short video loops whole plays, first play reported | ✅ 7 s H.264 film: played, reported `7000 ms` once, show re-timed | ⏳ | ⏳ |
| "Show on a screen" takes over, then the show resumes in step | ✅ unit test (player) | ⏳ | ⏳ |
| `identify`, `reload`, `clear-cache` each exactly once | ✅ identify on screen; all three in unit tests | ⏳ | ⏳ |
| Stop → idle screen (time, Ready, name, @owner) | ✅ | ⏳ | ⏳ |
| Over the licence count → notice, nothing plays; comes back by itself | ✅ | ⏳ | ⏳ |

## D. Presentation

| | Dev | Pi 5 | Pi 4 |
|---|---|---|---|
| `fit` BLUR (wash 0/60/100, shadow), FRAME (five styles), BLACK, FILL | ✅ BLUR 60, FRAME walnut + oak, FILL; others ⏳ | ⏳ | ⏳ |
| `marginPercent` 0 and 20 | ⏳ | ⏳ | ⏳ |
| Label: card / one line / none; timing; corners; toggles; legible over white and black | ✅ card, one line, description, two corners; legible over a light piece | ⏳ | ⏳ |
| QR: each target, four corners, none; scans to the right page | ✅ shown bottom-right (mock QR is a placeholder pattern) | ⏳ | ⏳ |
| `imageQuality` ORIGINAL on 4K visibly sharper than DISPLAY | ⏳ | ⏳ | ⏳ |
| Opening hours: black outside, panel in standby, takeover still shows | ✅ black (unit test); CEC ⏳ | ⏳ | ⏳ |
| Burn-in guard shifts label and QR every ~2 min | ⏳ | ⏳ | ⏳ |

## E. Resilience

| | Dev | Pi 5 | Pi 4 |
|---|---|---|---|
| Network pulled 5 min: keeps playing by the clock; re-syncs without a jump | ✅ (40 s outage: kept playing, orange dot) | ⏳ | ⏳ |
| Reboot with no network: plays the last playlist from cache | ✅ (agent restart) | ⏳ | ⏳ |
| A 404 piece is skipped for the lap; the error shows in state | ⏳ (a 404 copy falls back to the next copy ✅ in `test_store.py`) | ⏳ | ⏳ |
| 8 hours unattended: no memory growth that matters, no crash | ⏳ | ⏳ | ⏳ |
| Access token in no log, crash report or screenshot | ✅ token never leaves the agent; never logged (unit test checks the player's messages) | ⏳ | ⏳ |
| Commercial: outage → `plays` on the first poll back | ✅ (9 entries reported after the outage) | ⏳ | ⏳ |
| `state.visible` false outside hours and with nothing drawing; no plays then | ✅ unit test | ⏳ | ⏳ |

## F. Platform

| | Pi 5 | Pi 4 |
|---|---|---|
| Starts on power-on; no screensaver, cursor or blanking while playing | ⏳ | ⏳ |
| Image builds in CI and flashes with Raspberry Pi Imager ("Use custom") | ⏳ | ⏳ |
| Imager OS customisation (Wi-Fi) applies, then first boot partitions and goes read-only | ⏳ | ⏳ |
| Setup network appears after ~60 s with no network; captive page joins Wi-Fi | ⏳ | ⏳ |
| HDMI-CEC standby and wake; TV remote OK/Back reach the overlay | ⏳ | ⏳ |
| Update applied in a dark window; a broken release rolls back | ⏳ | ⏳ |

---

## Measurements to make on hardware

### 4K stills

On a 4K television, `imageQuality` ORIGINAL vs DISPLAY, `fit` BLUR, a detailed
original of 30+ megapixels:

| | Pi 5 | Pi 4 |
|---|---|---|
| Output mode chosen (`wlr-randr`) | | |
| Time from piece due to piece on the wall (debug panel) | | |
| Transition smooth at full frame rate (FADE 1200 ms, DISSOLVE) | | |
| Agent resize time for the original (`journalctl -u solpersona-agent`) | | |
| Chromium memory after 50 pieces (`ps -o rss`) | | |

### 4K video

The Pi 5 has no H.264 hardware decoder; Chromium decodes in software. The Pi 4
has one. For each: a 1080p30 and a 2160p30 H.264 MP4, and a 2160p30 VP9 WebM.

| | Pi 5 1080p H.264 | Pi 5 4K H.264 | Pi 5 4K VP9 | Pi 4 1080p H.264 | Pi 4 4K H.264 | Pi 4 4K VP9 |
|---|---|---|---|---|---|---|
| Plays smoothly (dropped frames: `chrome://media-internals` over DevTools) | | | | | | |
| CPU % (`top`, Chromium GPU + renderer) | | | | | | |
| Hardware decode used | | | | | | |

Advice that results in: e.g. `resolution = "1080p"` for video-heavy galleries
on a Pi 4.

### 8-hour soak

Pi 5 and Pi 4, a show of 40 stills and 5 films, 10 s slides, BLUR, DISSOLVE.
Record every hour: Chromium RSS, agent RSS, `/data` usage, restarts
(`journalctl -b | grep -c "starting it again"`), watchdog resets.

| Hour | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|---|
| Pi 5 Chromium RSS (MB) | | | | | | | | | |
| Pi 5 agent RSS (MB) | | | | | | | | | |
| Pi 4 Chromium RSS (MB) | | | | | | | | | |
| Pi 4 agent RSS (MB) | | | | | | | | | |

### Power-cut recovery

Pull the power 20 times at random moments — during a download, during an
update switch, during first boot after partitioning — and record whether the
screen comes back to the same state (pairing / playing) with its gallery.

| Moment | Runs | Came back | Notes |
|---|---|---|---|
| Playing, downloads running | | | |
| Pairing, code shown | | | |
| During an app update switch | | | |
| First boot, after partitioning | | | |
