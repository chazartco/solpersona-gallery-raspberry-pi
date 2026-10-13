# Questions and proposals

Things `SPEC.md` did not settle for the Pi, places where it, the reference
simulator and the build prompt disagree, and server changes this player would
like — proposed here, not invented. Each says what the player does today.

## Where the documents disagree

1. **Turning "Keep this gallery on the screen" off.** `CONFORMANCE.md` C2 says
   "Turn the option off: the stored copy is cleared", and SPEC §11a.5 says the
   device "returns to ordinary cache size limits". The build prompt says the Pi
   keeps the whole show on board *whether or not* `keepOffline` is set, and that
   `keepOffline` additionally means "never evict this show, even under the size
   cap".
   **Today:** the prompt is followed. With `keepOffline` off, the Pi still
   stores the whole show while it fits, and under pressure evicts the show's
   files due furthest away (a rolling window); with it on, the show's files are
   never evicted and the error says "Room for N of M". So the dashboard keeps
   showing "Stored on the screen: N of M" after the switch is turned off.
   **Question:** is that what the dashboard should show for a Pi, or should the
   Pi clear its copy when the switch goes off, as the checklist says?

2. **The mat colour.** SPEC §8 says warm white `#fbfaf7`; the simulator draws
   `#f5f3ee`. **Today:** `#fbfaf7` (the spec wins). The simulator could be
   brought into line.

3. **Out-of-range settings.** SPEC §8 says clamp; `screen-settings.ts` falls
   back to the default (`.catch`). **Today:** numbers are clamped (e.g.
   `washOpacity: 140` → 100), unknown enum values take the default.

4. **`state.visible` on the idle screen.** The simulator reports `true` while
   showing the idle clock; SPEC §4.1 says `true` "while it is showing work".
   **Today:** `false` when no piece is on the wall (idle, unlicensed), `true`
   while a piece is shown. It only matters for commercial screens, where idle
   has no show anyway.

4b. **Which part of a show that does not fit is stored.** SPEC §11a says "from
   the start of the timeline"; the simulator (and this player) store from the
   piece on the wall onwards, nearest-due first, as §11a.2 also asks. For a
   show that is already playing, the latter keeps the next pieces safe.
   **Today:** from the wall onwards. Which is intended?

## What the spec leaves open

5. **Time zone.** Opening hours are in "device local time", but a Pi only knows
   its zone if Raspberry Pi Imager set it. **Today:** Imager's setting, or
   `timezone` in `solpersona-gallery.toml`, else UTC.
   **Proposal:** `device.timeZone` (IANA name) in the sync response, set from
   the dashboard or from the owner's account, which a device uses when it has
   none of its own.

6. **Portrait screens and `screenWidth`.** A screen hung on its side
   (`rotation = 90`) reports `screenWidth: 2160, screenHeight: 3840` — the panel
   as the art sees it. **Question:** is that what the server expects for the
   `AUTO` quality rule, or should it be the panel's native orientation?

7. **`identify` while dark.** Outside opening hours the panel may be in CEC
   standby. **Today:** the name is drawn but the panel is not woken. Should
   `identify` wake the panel for its six seconds?

8. **Changing `storageMb`.** Free space moves with every download. **Today:**
   the reported value only changes when it moves by more than 5 % (or 64 MB),
   so `capabilities` are not resent on every poll.

9. **The sample gallery's files** are streamed, never stored on the card (it
   is not this screen's playlist). Tell us if it should play offline too.

10. **The setup network on a linked screen.** A linked screen that loses its
    Wi-Fi keeps playing from the card; if it has not been online since boot it
    opens the setup network and shows a small corner card ("No network — to
    connect this screen, join Wi-Fi SolPersona-Gallery-XXXX") over the art.
    Acceptable on a commercial wall?

11. **Video lengths "once per item per show version".** Reporting a length
    makes the server re-time the show, which is a new version; the player then
    reports the same length again once, the next time it loads the film. The
    server ignores it, but it could be avoided if a re-timing did not count as
    a new version for this rule. **Today:** literal reading of §6.4.

## Server additions we would use (proposals)

12. **A screen-sized copy.** Between `display` (~1000 px) and `original` (any
    size), a `large` copy (e.g. 3840 px on the long side, high-quality JPEG)
    would let a 4K screen look 4K-sharp without downloading 50-megapixel
    originals over a slow connection. **Today:** the Pi downloads the original
    and makes that copy itself (`imageopt.py`).

13. **Sizes in the playlist.** `bytes` per media URL in `ScreenItem.media`
    would let a device say "Room for 31 of 40 pieces" before downloading, and
    plan the order of downloads by size.

14. **Pixel size of each piece** (`width`, `height`) would let a player lay the
    piece out before it has decoded, and choose between copies.

15. **A wake hint.** `device.nextOpenAt` alongside `schedule`, so a screen that
    is dark can schedule waking the panel a little early (CEC wake-up of some
    TVs takes ~10 s).

## For the owners of this repository

16. **Licence.** `LICENSE` is "all rights reserved" (proprietary) as the build
    prompt asked, with permission to flash released images. The copyright
    holder is written as "SolPersona" — please put the legal entity's name.

17. **Release signing key.** Updates are off in any image built before
    `image/release-signing.pub` is committed. Run
    `scripts/make-signing-key.sh` once, store the private key in the
    `RELEASE_SIGNING_KEY` Actions secret, commit the public key.

18. **Hardware run.** `docs/CONFORMANCE-RESULTS.md` lists what was verified in
    development and what still needs a Pi 5 and a Pi 4 on a 4K television; the
    Wi-Fi setup, CEC, the overlay root and first boot in particular have not yet
    run on hardware.
