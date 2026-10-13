# How the Pi image works

The contract is [`solpersona/SPEC.md`](solpersona/SPEC.md); this page is how
this player meets it on a Raspberry Pi, and why it is built the way it is.

```
 ┌──────────────────────── Raspberry Pi OS Lite (Bookworm, arm64), read-only root ───────────────────────┐
 │                                                                                                         │
 │  solpersona-kiosk.service (user gallery, tty1)        solpersona-agent.service (user gallery)            │
 │  ┌──────────────────────────────────────┐            ┌────────────────────────────────────────────┐     │
 │  │ cage ── kiosk.sh ── Chromium --kiosk │  ws + http │ Python 3 · aiohttp · 127.0.0.1:8737          │     │
 │  │          player/ (TypeScript, Vite)  │◀──────────▶│ pairing · sync · clock · commands · plays    │────▶ SolPersona
 │  │  clock · tick · cache · renderer     │  /media    │ media store · QR · CEC/HDMI power · Wi-Fi    │     device API
 │  └──────────────────────────────────────┘  /qr       └────────────────────────────────────────────┘     │
 │                                                        token: /var/lib/solpersona-gallery/device.json     │
 │                                                        media: /var/cache/solpersona-gallery/media         │
 └────────────────────────────── /data (ext4, the only writable partition) ────────────────────────────────┘
```

## Two halves

**The player** is the reference simulator's player, ported section by section
to TypeScript (`player/src`): the clock (`timeline.ts`, checked against
`test-vectors.json`), the tick (`player.ts`, section 7), the cache
(`cache.ts`, section 5), the renderer (`renderer.ts`, section 6: two layers,
every `fit`, the drawn frames and mat, the label, the QR card, FADE / DISSOLVE /
CUT) and the screens (`view.ts`). The simulator's development chrome (gear
menu, resolution picker, casting) is gone; its debug panel stays, behind `D`
when `debug = true` and behind **Show debug** in the remote's overlay. It is
built with Vite into static files — fonts included — and baked into the image,
so the player itself needs no network.

**The agent** (`agent/solpersona_gallery`) is everything a browser cannot do
well, and everything that must not live in a browser:

| | |
|---|---|
| `controller.py` | the state machine of SPEC §3–§7 and §11b: register → show the code → poll `/token` → store the token → poll `/sync`; the sample gallery; commands once by `seq`; video lengths once per version; `plays` while offline; `401` → back to pairing; `429` → `Retry-After`; backoff 3 → 60 s |
| `clock.py` | server time (§5.1) against the **monotonic** clock, median of five |
| `store.py` | the gallery on the card (§11a), nearest-due first, two at a time, LRU and size cap |
| `imageopt.py` | screen-sized copies of very large originals |
| `display.py` | the panel's real size from KMS/EDID; power over HDMI-CEC, else HDMI off, else black; the TV remote's keys |
| `network.py` | Wi-Fi setup: the `SolPersona-Gallery-XXXX` network and its captive page |
| `server.py` | the local face: `/`, `/ws`, `/media`, `/qr`, `/healthz` on 127.0.0.1 |
| `updater.py` | signed app updates with rollback; weekly OS security updates |

They talk over a WebSocket on 127.0.0.1. The agent sends `state` (mode,
status, registration, takeover, network, a server-time stamp), `show` (when it
changes), `stored` (the URLs on the card) and events (`identify`, `reload`,
`clear-cache`, remote keys). The player sends a `report` each second — what is
on the wall, whether it is visible, what is cached — plus measured video
lengths and actions (sample, stop sample, forget). **The access token never
crosses**: the player has no way to reach SolPersona with it, so it cannot leak
from a page, a crash dump or DevTools.

### Why Python for the agent

Bookworm ships `python3-aiohttp` (3.8) and `python3-pil` (9.4) as Debian
packages: the agent installs with `apt`, gets security fixes from Debian with
the rest of the OS, needs no compiler, no vendored binaries and no
cross-compilation in CI, and its tests run in CI against exactly the versions
on the Pi (`debian:bookworm-slim`, see `agent/Dockerfile.test`). Go would give
one static binary but would make us the owner of its dependencies' security
updates; Pillow (for screen-sized copies of huge originals) has no equal there.
The agent's work is network- and disk-bound; Python's speed is not the limit.

## The clock

The Pi has no battery-backed clock (the Pi 5's needs a cell fitted), so its
wall clock is wrong until NTP answers and then jumps. Server time is therefore
kept as an offset from the **monotonic** clock in the agent (median of the last
five samples, SPEC §5.1), stamped on every message to the page, and carried
into the page against `performance.now()`. An NTP step moves nothing on the
wall. Across a reboot the agent seeds the offset from the last known
server-minus-wall difference (in `show.json`), and `systemd-timesyncd`'s clock
file lives on the data partition, so even a reboot with no network starts near
the right time. Opening hours are evaluated in the device's time zone at
*server* time.

## The gallery on the card (SPEC §11a)

The Pi always keeps the whole show: as soon as a show arrives the agent
downloads every piece in timeline order from the piece on the wall, two at a
time, while the wall plays at once — a piece not yet stored is redirected to
its host (`/media` → `302`). It reports `persistentStorage: true`, an honest
`storageMb` (the `cache_mb` setting, or most of the free space less a reserve)
and `state.stored` / `state.total` on every sync.

* For a still: the file §6.2 chooses (the original on a 4K panel) plus its
  small display copy, which the BLUR wash is drawn from. For a film: the film
  and its still.
* An original much larger than the panel is stored as a copy that just covers
  the panel (FILL is the largest any `fit` draws it), at JPEG quality 94 with
  4:4:4 chroma, its colour profile and EXIF orientation applied — Chromium on a
  Pi then never decodes a 50-megapixel file in a transition. Animated, SVG and
  AVIF files are kept as they came. A larger panel plugged in later gets the
  originals fetched again.
* Room: files nothing wants go first (least recently used); then, unless the
  owner switched on **Keep this gallery on the screen** (`keepOffline`), the
  show's files due furthest away. With `keepOffline` the show is never evicted;
  a show that does not fit is stored from the wall onwards and `state.error`
  says "Room for 31 of 40 pieces".
* A new version keeps what it still uses, fetches what is new, deletes the rest.
* Downloads are written to `partial/`, fsynced and renamed; the index is
  rewritten whole; on start the two are reconciled. A power cut loses at most
  the download in flight.
* QR codes (§9) are fetched by the agent with the token, for every piece of the
  show, and kept for the show version — so they too survive a reboot offline.

## The card: read-only root, one data partition

A screen's power is pulled, not shut down. So:

* **First boot** (`firstboot.sh`, once): the root partition grows to a fixed
  6 GiB (4.5 GiB on cards under 16 GB) — room for years of security updates —
  and the rest of the card becomes the ext4 partition `spgdata`, mounted at
  `/data`. Wi-Fi set in Raspberry Pi Imager moves onto it. Then the root
  becomes a read-only overlay and the Pi reboots. (Imager's own first-run step,
  if used, happens in a boot before this one.)
* **Every boot after**: `/` is Raspberry Pi OS's `overlayroot` (writes go to
  RAM), with `recurse=0` so that `/data` stays a real disk. Bind mounts put the
  gallery's state, cache, app updates, saved Wi-Fi and the time-sync clock file
  there. `raspi-config` would write the overlay option without `recurse=0`,
  overlaying `/data` too, so `overlay.sh` sets it.
* `/data` is ext4 with a journal and `commit=30`; every file the agent writes
  is written whole and renamed into place.
* Logs are in memory (`Storage=volatile`, 48 MB cap).

## Staying up

| What fails | What happens |
|---|---|
| Chromium exits or crashes | `kiosk.sh` (inside cage) starts it again at once |
| The page hangs | the agent stops getting reports; after 2 minutes `kiosk.sh` kills Chromium (if the agent is healthy) and starts it again |
| cage exits | systemd restarts `solpersona-kiosk` |
| The agent crashes | systemd restarts it within a second; the page keeps playing by its last clock meanwhile and reconnects |
| The agent's loop hangs | it stops petting systemd's watchdog (`WatchdogSec=30`) and is restarted |
| The whole system hangs | systemd stops petting the hardware watchdog (`RuntimeWatchdogSec=15s`, `dtparam=watchdog=on`); the board resets |
| The network drops | the wall plays on by the clock from the card; a small orange dot; `plays` are recorded and sent on reconnect |
| Power is cut | the root is RAM-backed; `/data` is journalled and written atomically |

## Screen power (SPEC §8 `schedule`)

Outside opening hours (with no takeover) the agent sends the panel to standby
over HDMI-CEC (`cec-ctl --standby`) and wakes it at opening time
(`--image-view-on`, `--active-source`); where CEC is absent or unanswered it
switches the HDMI output off with `wlr-randr` (most panels then sleep). The
player draws black in any case and reports `state.visible: false`. `screen_off`
in the settings file forces one method.

## Wi-Fi setup

Ethernet and Imager-configured Wi-Fi are NetworkManager's job. If nothing is
reachable ~60 s after boot, no cable is plugged in, and the screen has not been
online since boot (or knows no network), the agent opens an access point
`SolPersona-Gallery-XXXX` (NetworkManager "shared" mode, 2.4 GHz). dnsmasq
answers every name with the Pi, so phones open the setup page by themselves;
it lists the networks seen and takes a password. A screen that already knows a
network steps out of setup mode every five minutes (while nobody is using the
page) to look for it — routers boot slower than Pis after a power cut. A
not-yet-linked screen shows the setup screen full size; a linked screen playing
from the card shows a small corner card instead.

## Updates

* **App**: `solpersona-updater.timer` runs the updater (from the read-only
  root, never from an updated copy). Once a day it asks this repository's
  latest GitHub release; a newer version is accepted only if `SHA256SUMS` has a
  valid Ed25519 signature by the key in the image and the bundle matches; it is
  unpacked beside the current one in `/var/lib/solpersona-gallery-app/` and
  switched to (a symlink) at the next dark window or 03:00 local, and only while
  the running version is syncing fine. If the new version does not report a
  successful sync within five minutes it is switched back and marked bad. The
  image-level files (systemd units, kiosk script) change only with a new image.
* **OS**: weekly, in a dark window, the overlay is switched off and the Pi
  reboots; `unattended-upgrade` applies Debian security updates only; the
  overlay is switched back on and the Pi reboots again. apt's own timers are
  masked.

## Video

H.264 MP4 and VP9 WebM play in Chromium. The Pi 4 decodes H.264 in hardware;
the **Pi 5 has no H.264 decoder** (it has HEVC only, which SolPersona does not
send), so H.264 is decoded in software: comfortable at 1080p, to be measured at
4K (`docs/CONFORMANCE-RESULTS.md`). A film Chromium cannot play shows its
still for the slot. Films are never cut off (§6.3): an estimated slot is held
until the play ends, capped at the film's length + 5 s, unless a person moved
the clock.
