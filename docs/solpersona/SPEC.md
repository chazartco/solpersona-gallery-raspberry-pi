# SolPersona Gallery — device integration specification

**Version 1** · protocol `v1` · October 2026

This is everything a device needs to become a SolPersona gallery screen: a
television, a set-top box, a Raspberry Pi behind a monitor, a projector at an
opening. It is written to be read without access to SolPersona's own code. The
reference implementation is `reference/device-simulator.html` in this folder —
when this document and that file disagree, raise it; the intent of this document
wins.

---

## 1. What a screen does

A screen is a dumb, beautiful wall. It never decides what to show. It:

1. **Pairs** once: asks SolPersona for a code, shows it as a QR code, waits while
   a person scans it with their phone and links the screen to their account, then
   receives its own credential.
2. **Polls** every few seconds: says what it is doing, and is told what to play —
   a playlist, a shared clock, the gallery's presentation settings, and any
   one-off commands.
3. **Plays by the clock**: works out from the clock which piece should be on the
   wall *right now*, shows it with the gallery's framing, label and QR code, and
   pre-loads what comes next.

Several screens playing the same show are in step because they share the clock —
they never talk to each other. Every control a person presses in the dashboard
(pause, next, jump, restart) arrives as a new clock on the next poll. A screen that
misses a poll is out of step for one poll at most, and never has a queue of
instructions to replay.

```
 ┌──────────── first run ────────────┐   ┌──────────── forever ─────────────┐
 register → show QR → poll /token ──▶ token stored → poll /sync every 3 s ──▶
                       (person links on phone)       │
                                                      ├─ play by the clock
                                                      ├─ pre-load next pieces
                                                      ├─ report video lengths
                                                      └─ carry out commands
```

---

## 2. Conventions

* **Base URL.** Production is `https://solpersona.com`. It must be configurable
  (development stacks run at `http://localhost:8080` and on LAN addresses). Every
  path below is relative to it.
* **JSON** in and out, UTF-8. Send `Content-Type: application/json` on POSTs with a
  body.
* **Errors** always look like:
  ```json
  { "error": { "code": "SCREEN_NOT_LINKED", "message": "A sentence a person can read." } }
  ```
  Switch on `code`, never on `message`. Validation failures are `422` with
  `code: "VALIDATION_FAILED"` and a `details` array.
* **Rate limits** answer `429` with `code: "RATE_LIMITED"` and a `Retry-After`
  header (seconds). Wait that long, then carry on.
* **CORS**: the device API answers any origin (`Access-Control-Allow-Origin: *`) and
  never uses cookies. Web-based players (Tizen, a browser kiosk) work cross-origin.
* **Times** are Unix epoch **milliseconds** (numbers) or ISO-8601 strings where
  stated.
* **Unknown fields**: ignore any field you do not recognise, in every response.
  New fields are added without a protocol bump; removing or changing one is a bump.
* **Never log or display the access token**, and never send it anywhere but the
  device API on the configured base URL.

---

## 3. Pairing

### 3.1 Register — `POST /api/v1/device/register`

No credential. Body (all optional, but send them):

```json
{
  "platform": "roku",                 // ≤ 60 chars: raspberry-pi | roku | samsung-tizen | …
  "appVersion": "1.0.0",              // ≤ 40 chars
  "name": "Living room",              // ≤ 60 chars, suggested name; the person can change it
  "capabilities": {
    "screenWidth": 3840,              // the panel's real pixels, not the UI resolution
    "screenHeight": 2160,
    "storageMb": 2000,                // room the app will give to cached files
    "video": true,                    // can it play H.264 MP4 at all
    "originals": true,                // has the memory/decoder to show full-size originals
    "persistentStorage": true         // can keep a whole show across restarts (§11a)
  }
}
```

`201`:

```json
{
  "deviceId": "6f0c…",                // this screen's id (show it small, for support)
  "deviceCode": "k3Jv…",              // SECRET. Keep in memory/storage; never display.
  "pairingCode": "ABCD-EFGH",         // show this large
  "pairUrl": "https://solpersona.com/pair?code=ABCD-EFGH",
  "qrDataUri": "data:image/png;base64,…",  // a ready QR of pairUrl; or draw your own from pairUrl
  "expiresAt": "2026-10-12T18:15:00.000Z", // 15 minutes
  "intervalMs": 3000                  // how often to poll /token
}
```

### 3.2 The pairing screen

Full screen, legible from across a room:

* Heading: **"Register this screen to your SolPersona account"**.
* The QR code (from `qrDataUri`, or generated from `pairUrl`), large.
* "Scan the code with your phone, or go to **solpersona.com/pair** and enter" and the
  `pairingCode` very large (the alphabet has no `0 O 1 I`).
* A small countdown to `expiresAt`, and the `deviceId` small at the bottom.

### 3.3 Wait — `POST /api/v1/device/token`

Every `intervalMs`, body `{ "deviceCode": "…" }`:

| Response | Meaning | Do |
|---|---|---|
| `202 { "status": "PENDING", "expiresAt": "…" }` | nobody has linked it yet | keep polling |
| `200 { "status": "LINKED", "accessToken": "sps_…", "device": { "id", "name" } }` | linked | **store the token immediately, durably**, then start the poll loop (§4) |
| `401 TOKEN_EXPIRED` | the code ran out | register again (§3.1) silently, show the new code |
| `401 SCREEN_NOT_LINKED` | the code is unknown or already exchanged | register again |
| `429` | too fast | wait `Retry-After` |
| network error | — | keep showing the code, retry with backoff (max 30 s) |

The access token is handed over **exactly once**. If the app crashes between
receiving and storing it, the screen must be paired again — so store it before doing
anything else.

### 3.4 What the person sees

They scan, sign in on their phone if needed, name the screen ("Front window") and
press **Link this screen**. If their account has no free licence they are told so
and the screen simply keeps waiting; it can be linked the moment a licence is
bought. Nothing for the device to do differently.

---

## 4. The poll — `POST /api/v1/device/sync`

`Authorization: Bearer sps_…`. Every `pollMs` (3000 today — always use the value
the server sends). This one request is the heartbeat, the instruction channel and
the report.

### 4.1 Request

All fields optional; send what you have.

```json
{
  "showId": "a7e1…",          // the show you currently hold, or null
  "version": 4,               // its version, or null — the playlist is only sent when these differ
  "ackSeq": 7,                // highest command seq you have carried out (§7)
  "durations": [              // video lengths you measured since the last sync (§6.4), ≤ 50
    { "itemId": "a:9c2e…", "ms": 12480 }
  ],
  "state": {                  // free-form, ≤ 4 KB, shown live on the owner's dashboard
    "itemId": "a:9c2e…", "index": 3, "paused": false,
    "cached": 5, "bufferedAhead": 2, "error": null, "visible": true,
    "stored": 12, "total": 40     // kept on the device (§11a), when keepOffline
  },
  "capabilities": { … },      // as at registration, when they change (e.g. storage freed)
  "appVersion": "1.0.1",
  "plays": [                  // only after an outage: what played while out of touch (below), ≤ 2000
    { "itemId": "a:9c2e…", "startedAt": 1760289000000, "ms": 120000 }
  ]
}
```

**`plays` — reporting time played offline.** A screen keeping its gallery on board
(§11a) plays straight through a network outage. On a commercial screen the artists are
paid for the time their work was on the wall, so while polls are failing record what is
visibly on screen — one entry per stretch of one piece: its `itemId`, when it came up
(`startedAt`, **server** time from your clock, §5) and how long it stayed (`ms`) — and
send the log with the first poll that succeeds, then discard what you sent. Record
nothing while dark (outside `schedule`) or backgrounded. The server accepts only time
inside the gap it did not see, only for pieces it gave you to play, and never more in
total than the gap; anything else is ignored, so sending more than you should gains
nothing. Private screens may send it too; it is ignored.

Record `sentAt` (local ms) just before sending and `receivedAt` just after the
response arrives — the clock needs both (§5.1).

**`state.visible` must be honest.** Send `false` whenever the screen is dark — outside
its `schedule`, in standby, or with the app in the background — and `true` while it is
showing work. On a *commercial* screen (one an account placed at a venue) artists are
paid by the time their work was visibly on screen, and the server counts only polls
that say `visible` is not `false`. A device that never sends the field is treated as
visible. Nothing else in the protocol differs for a commercial screen: it is paired the
same way and plays whatever show it is sent (its `sourceRef` reads `curated:<id>`).

### 4.2 Response

```json
{
  "serverTime": 1760290000000,
  "pollMs": 3000,
  "device": { "id": "6f0c…", "name": "Front window", "owner": "chaz", "keepOffline": false },
  "status": "PLAYING",                       // IDLE | PLAYING | UNLICENSED
  "show": {
    "id": "a7e1…",
    "version": 5,
    "sourceRef": "collection:…",             // informational
    "epochMs": 1760289000000,                // the clock (§5)
    "pausedAtMs": null,
    "settings": { … },                       // ┐ present ONLY when the request's
    "items": [ ScreenItem, … ],              // │ showId/version differ from these —
    "timeline": [ { "id": "a:…", "ms": 30000 }, { "id": "a:…", "ms": 21000, "estimated": true } ]
  },                                          // ┘ otherwise keep what you hold
  "takeover": { "item": ScreenItem, "settings": { … }, "startedAt": "…", "until": null },
  "commands": [ { "seq": 8, "type": "identify", "issuedAt": "…" } ]
}
```

* `show` is `null` when nothing is playing; `takeover` is `null` when there is none.
* `status`:
  * `IDLE` — linked, nothing to play. Show a calm idle screen: the time, "Ready",
    the screen's name and `@owner`. No QR code.
  * `PLAYING` — play `show` (and/or `takeover`).
  * `UNLICENSED` — the account has more screens than licences and this one is not
    covered. Show **"This screen isn't active on your account yet."** and, smaller,
    "The account owner can manage their screens at solpersona.com." (§11c — never
    mention buying). Play nothing; keep polling. It comes back on its own when the
    owner covers it or removes another screen.
* When `show.id` or `show.version` changes, replace the playlist atomically
  (settings, items, timeline together). Keep whatever is on the wall if it is still
  in the new playlist — the server re-times shows without moving the piece on screen.

### 4.3 Errors on sync

| | Do |
|---|---|
| `401 SCREEN_NOT_LINKED` or `401 UNAUTHENTICATED` | the screen was removed from its account: **delete the stored token** and go back to pairing (§3) |
| `429` | wait `Retry-After` |
| `5xx`, timeout, no network | **keep playing** by the last clock (local time + last offset); retry with backoff 3 s → 6 → 12 → … max 60 s; show a small unobtrusive offline dot. Never blank the wall because the network blinked. |

Persist the last playlist and clock so a screen that reboots without network still
plays.

---

## 5. Playing by the clock

`reference/screen-timeline.ts` is the algorithm, in 150 lines of TypeScript. Port it
line for line; `test-vectors.json` checks the port.

### 5.1 Server time

```
sample  = serverTime − (sentAt + receivedAt) / 2
offset  = median of the last 5 samples           (or a smoothed average)
now()   = localMonotonicMs + offset               (use a monotonic source where the platform has one)
```

### 5.2 Position

```
total = Σ timeline[i].ms
at    = (pausedAtMs ?? now()) − epochMs
lap   = floor(at / total)
into  = at − lap·total
walk the timeline subtracting each slot's ms until `into < slot.ms`
→ { index, offsetMs: into, lap }
```

Recompute every 250 ms or so (and on every sync). When `index` changes, transition
to `timeline[index].id`'s item. `pausedAtMs` set means paused: the position stays
put. That is the entire playback logic — pause, resume, next, previous, jump,
restart and re-timing all arrive as a new `epochMs`/`pausedAtMs`.

### 5.3 Rules

* **Never transition to a piece that has not loaded.** Hold the current piece; the
  clock decides the next slot.
* **Takeover** (§5.4) draws over the show; the show's clock keeps running underneath.
* A clock that jumps (a person pressed next) is followed immediately, with the normal
  transition.
* Shuffle is already applied by the server — play `timeline` in order.

### 5.4 Takeover ("show this now")

When `takeover` is non-null, show `takeover.item` full-screen using
`takeover.settings`, until `takeover.until` (ISO time, compare with server time) or
until a sync arrives with `takeover: null`. Then return to the show at the clock's
*current* position — the screen rejoins its group exactly in step.

---

## 6. Media

### 6.1 `ScreenItem`

```ts
{
  id: string;          // stable; prefixes a: h: p: g: — treat as opaque
  kind: 'IMAGE' | 'VIDEO';
  title: string;
  artist: { name: string; handle: string | null } | null;
  year: number | null;
  description: string | null;
  media: {
    original: string | null;  // the artist's full file; for VIDEO, the film
    display: string | null;   // ~1000 px WebP copy of a still (or of a video's poster)
    poster: string | null;    // ~600 px still: placeholder, and the wash for a video
    fallbacks: string[];      // the same file on another host, tried in order when one fails
  };
  links: { solpersona: string | null; vault: string | null; marketplace: string | null };
}
```

URLs are absolute, public `https://` (in development they may be `http://`). No
authentication header is needed — or wanted — for media.

### 6.2 Which file to show for a still

| `settings.imageQuality` | file |
|---|---|
| `ORIGINAL` | `original ?? display` |
| `DISPLAY` | `display ?? original` |
| `AUTO` | `original` when `capabilities.screenWidth > 1280` **and** the device can hold an original in memory; otherwise `display ?? original` |

On failure try the next candidate, then `fallbacks`, then `poster`. If nothing
loads, skip the piece for this lap (the clock moves on) and report `state.error`.

Formats to expect: JPEG, PNG, GIF (possibly animated), WebP, and occasionally AVIF or
SVG for originals; `display`/`poster` are WebP. A platform that cannot decode a format
falls back to the next candidate. An original can be very large (tens of
megapixels) — decode at screen size where the platform allows it.

**Generative / HTML artworks never run on a screen.** They arrive as `IMAGE` with
their still; there is nothing to execute.

### 6.3 Video

* `media.original` is the film: **MP4 (H.264/AAC) or WebM (VP9)**. If the platform
  cannot play it, show `display ?? poster` as a still for the slot.
* Muted unless `settings.videoSound` is true.
* Start at `offsetMs mod durationMs` so a screen joining mid-slot is in step; loop
  whole plays until the slot ends.
* **Never cut a video off.** If the slot ends mid-play and the slot is
  `estimated: true`, let the current play finish before moving on — unless the clock
  was moved by a person (a different `epochMs` arrived), which is followed at once.
  Cap any such hold at the video's length + 5 s.

### 6.4 Reporting video length

The first time you learn a video's duration (metadata loaded), add
`{ itemId, ms }` to the next sync's `durations` — once per item per show version.
The server re-times the show around the real length **without moving what is on
screen**. Report promptly: a late report can land after the estimated slot has ended.

### 6.5 Caching and pre-loading

* Keep: the piece on the wall, the previous piece, and the next `settings.prefetch`
  pieces in timeline order (wrapping). Evict everything else.
* A device with room may cache the whole show on disk, keyed by URL; keep originals
  across reboots if storage allows (the Pi should).
* At most two downloads at once, nearest-due first.
* Report `state.cached` (ready pieces) and `state.bufferedAhead` (ready pieces ahead).

---

## 7. Commands

Each sync may carry `commands: [{ seq, type, issuedAt }]`, the ones not yet
acknowledged. Carry each out **once** (remember the highest `seq` done), then send
`ackSeq` on the next sync.

| `type` | Do |
|---|---|
| `identify` | show the screen's name very large, over everything, for ~6 s ("which one is which" on a wall of six) |
| `reload` | drop the held playlist (send `showId: null, version: null` next) and re-download |
| `clear-cache` | delete cached media (keep what is on the wall), then re-fetch as needed |

Ignore unknown types (but still acknowledge them).

---

## 8. Presentation settings

`show.settings` (and `takeover.settings`) is the full resolved object — every key is
present. Unknown keys: ignore. Out-of-range values: clamp.

| Key | Type / values (default) | Meaning |
|---|---|---|
| `slideSeconds` | 5–3600 (30) | already baked into `timeline`; informational |
| `order` | `GALLERY` \| `SHUFFLE` | already applied to `timeline` |
| `transition` | `FADE` \| `DISSOLVE` \| `CUT` (`FADE`) | FADE: out to black then in; DISSOLVE: cross-fade; CUT: instant |
| `transitionMs` | 0–5000 (1200) | length of the fade / dissolve |
| `loopShortVideos`, `maxVideoLoops` | bool (true), 1–20 (3) | baked into `timeline` |
| `videoSound` | bool (false) | unmute video |
| `fit` | `BLUR` \| `FRAME` \| `BLACK` \| `FILL` (`BLUR`) | see below |
| `washOpacity` | 0–100 (60) | BLUR: opacity of the blurred wash over black |
| `pieceShadow` | bool (true) | BLUR: soft drop shadow under the piece |
| `frameStyle` | `GALLERY_WHITE` \| `MUSEUM_BLACK` \| `OAK` \| `WALNUT` \| `NONE` | FRAME: moulding (NONE = mat only) |
| `marginPercent` | 0–20 (4) | space kept clear around the piece, % of the screen's shorter side |
| `imageQuality` | `AUTO` \| `ORIGINAL` \| `DISPLAY` | §6.2 |
| `captionStyle` | `LABEL` \| `MINIMAL` \| `NONE` (`LABEL`) | wall label: museum card / one line / none |
| `captionTiming` | `ALWAYS` \| `START` | START: first ~8 s of each piece, then fade out |
| `captionCorner`, `qrCorner` | `BOTTOM_LEFT` \| `BOTTOM_RIGHT` \| `TOP_LEFT` \| `TOP_RIGHT` | |
| `showTitle`, `showArtist`, `showYear`, `showDescription` | bool (true, true, true, false) | what the label carries (description: museum card only, ~4 lines) |
| `qrTarget` | `SOLPERSONA` \| `VAULT` \| `MARKETPLACE` \| `NONE` | NONE: no code; otherwise fetch §9 |
| `burnInGuard` | bool (true) | shift the label and QR a few pixels every ~2 min (OLED care) |
| `prefetch` | 1–10 (2) | §6.5 |
| `schedule` | `{ on: "HH:MM", off: "HH:MM" }` \| null | outside these hours (device local time; `off` < `on` spans midnight; `on` = `off` means always on) show black and, where possible, put the panel in standby; null = always on. A takeover is still shown. |

**Redraw on change.** When a new version arrives whose presentation keys differ
(`fit`, `washOpacity`, `pieceShadow`, `frameStyle`, `marginPercent`, `imageQuality`,
the caption and QR keys, `videoSound`), redraw the piece on the wall with them at once
— do not wait for the next piece.

**`fit`, precisely:**

* `BLUR` — the piece whole (`contain`), centred, inside `marginPercent`. Behind it the
  same image `cover`-scaled to the screen, heavily blurred (~18 px at 1080p, scaled
  1.15× to hide soft edges), at `washOpacity`% over black. `pieceShadow`: a soft shadow
  under the piece. For a video, the wash is its `display ?? poster`.
* `FRAME` — a mat (warm white `#fbfaf7`) around the piece and a moulding around the
  mat (white, black, oak, walnut), on a dark wall; the framed piece is contained.
* `BLACK` — the piece contained on black.
* `FILL` — the piece `cover`-scaled to the whole screen (crops). No margin.

**The wall label** must stay legible over any picture: the museum card sits on a dark
translucent panel; the one-line label sits on a dark translucent pill — never bare
text over the artwork. Title in an italic serif; artist; year in muted tone.
Typography should be generous: designed to be read from three metres.

---

## 9. QR codes — `GET /api/v1/device/qr?item=<itemId>`

`Authorization: Bearer sps_…`. Returns `image/png` (360 px) of the piece's link under
the gallery's `qrTarget` (falling back SolPersona → TheVault → marketplace when the
chosen one does not exist). The URL encoded is also in the `x-qr-link` response
header. `404` means "no code for this piece" — show none.

Only items currently in the screen's show or takeover are answered. Cache per
`itemId` + `qrTarget` for the life of the show version. Render on a white card with a
small "Scan to view" caption, in `qrCorner`. Media URLs never need the token; **this
endpoint does** — so a platform whose image views cannot send headers must download
the PNG itself and display it from local storage.

---

## 10. Forgetting — `DELETE /api/v1/device`

`Authorization: Bearer sps_…` → `204`. For a "Forget this account" / factory-reset
option in the app's own menu. Delete the stored token and go back to pairing.

---

## 11. Device app requirements (all platforms)

* **Starts by itself** at power-on where the platform allows, straight into the last
  state (pairing, idle or playing). No menus on the way.
* **Never sleeps** while playing: disable the screensaver / power saving; honour
  `schedule` instead.
* **Survives**: crash → restart (watchdog); network loss → keep playing (§4.3);
  reboot without network → play the persisted playlist and cached media.
* **Remote / keyboard** (where there is one): OK/Enter shows a small overlay with the
  screen name, owner, connection status and a **Forget this account** action; Back
  hides it. Nothing else is needed — the wall is controlled from the dashboard.
* **Settings the app itself keeps**: base URL (hidden/advanced; default
  `https://solpersona.com`), suggested screen name, a debug overlay toggle.
* **Debug overlay** (developer use): last sync status and latency, clock offset, show
  id/version, position, slot progress, cache contents, pending commands — the
  simulator's `D` panel is the model.
* **Secrets**: the access token lives only in the platform's private storage; never
  in logs, crash reports, screenshots or analytics.
* **Performance**: transitions at full frame rate; decode the next piece before it is
  due; a 4K panel should show 4K-sharp stills where `imageQuality` allows.
* **Accessibility of the pairing screen**: high contrast, very large code.

## 11a. Two ways a device holds the art — streaming and kept on the device

Every device works in one of two modes, and reports which it can do:

* **Streaming** (`capabilities.persistentStorage: false`) — a Roku with no memory
  card, a TV with a small cache. Pieces are fetched as they come up, at the largest
  size the device can show (`imageQuality` rules, §6.2), plus `prefetch` ahead. **The
  wall never changes to a piece that has not finished loading**: the current piece
  stays up until the next is ready. On a slow connection this means a screen may fall
  behind its group for a slot; when the next piece arrives it joins the clock where the
  clock is.
* **Kept on the device** (`capabilities.persistentStorage: true`, with
  `capabilities.storageMb` the room it has) — a Raspberry Pi, a Roku with a USB stick or
  SD card, BrightSign. When the owner switches on **"Keep this gallery on the screen"**
  in the dashboard, sync answers `device.keepOffline: true`. The device then:
  1. starts playing **at once** with the first piece (streaming it if needed);
  2. downloads **every** item of the show in timeline order (originals where
     `imageQuality` allows, else display copies; videos too) into persistent storage,
     two at a time, nearest-due first;
  3. reports progress in `state.stored` (items kept) and `state.total` (items in the
     show) on every sync — the dashboard shows "Stored on the screen: 12 of 40";
  4. plays from storage from then on, and keeps playing **with no network at all**,
     across reboots, until the show changes;
  5. when a new show version arrives, keeps every file still in it, downloads what is
     new, and deletes what is no longer used; when `keepOffline` goes false, returns to
     ordinary cache size limits.
  If the show does not fit in `storageMb`, store as much as fits from the start of the
  timeline and say so in `state.error` ("Room for 31 of 40 pieces").

## 11b. Try a sample gallery

The pairing screen offers **Try a sample gallery** (a button, or the remote's OK). It
plays SolPersona's public sample show — generative pieces made by the server — so a
person (and a store reviewer) sees the app working before linking anything.

`GET /api/v1/device/sample` — **no credential**. Answers the same shape as a sync
response's `show`, fully populated:

```json
{
  "serverTime": 1760290000000,
  "pollMs": 60000,
  "show": {
    "id": "sample", "version": 3, "sourceRef": "sample",
    "epochMs": 1760227200000, "pausedAtMs": null,
    "settings": { … }, "items": [ ScreenItem, … ], "timeline": [ … ]
  }
}
```

* Play it exactly like a show (§5). Its epoch is the start of the UTC day, so every
  screen showing the sample is in step with every other.
* Re-fetch every `pollMs` (a minute) — or when `version` changes — while it plays.
* Its QR codes come from `GET /api/v1/device/sample/qr?item=<itemId>` (no credential;
  same PNG answer as §9) and lead to SolPersona's page about the gallery app.
* Keep the pairing code visible while the sample plays (a small card in a corner:
  "Link this screen — solpersona.com/pair · ABCD-EFGH" plus the small QR), keep polling
  `/token`, and switch to the person's own gallery the moment it is linked.
* Leave the sample with Back / a "Stop sample" control, returning to the full pairing
  screen.

## 11c. Wording on the device (store-safe)

Store rules restrict pointing people to purchases outside the app. The app sells
nothing — every account has a screen free — and the device's words must never mention
buying, prices or plans. Use exactly:

* **Unlicensed:** "This screen isn't active on your account yet." · smaller: "The
  account owner can manage their screens at solpersona.com." No button, no price.
* **Pairing:** "Register this screen to your SolPersona account" · "Scan the code with
  your phone, or go to solpersona.com/pair and enter".
* **Idle:** "Ready" · the screen's name · "@owner".
* Anywhere: never "buy", "upgrade", "subscribe", "licence purchase", "$".

## 12. Versioning

This is protocol **v1**. A device sends `appVersion`; SolPersona will keep v1
compatible by only adding fields. A breaking change will be announced with a new
document and a deprecation window.

## 13. Getting a test account

Use a development stack (`http://<host>:8080`) or ask the SolPersona team for a
staging account. Pair the device, then from **Dashboard → Gallery Exhibition** choose
a gallery, press play, pause, next, "show this now" from an artwork page, change
settings (fit, label, QR, wash, schedule), and put two screens in step — every one of
those must be visibly correct on the device. `CONFORMANCE.md` is the checklist.
