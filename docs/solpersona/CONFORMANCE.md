# Conformance checklist

A player is ready to ship when every line here is true on the real hardware. Use a
development or staging SolPersona account and the **Gallery Exhibition** page
(`/dashboard/screens`). Keep the reference simulator open beside the device — the two
should look and behave the same.

## A. Automated

- [ ] The port's clock passes every case in `test-vectors.json` (`positionAt`,
      `clockOffset`, `imageChoice`, `schedule`) in the repo's own test suite, in CI.
- [ ] Unit tests cover: sync response handling (new version vs same version), command
      de-duplication by `seq`, `401 SCREEN_NOT_LINKED` → back to pairing, `429` →
      honours `Retry-After`.

## B. Pairing

- [ ] First boot goes straight to the pairing screen (no menus), with the QR, the code
      and a countdown, legible from 3 m.
- [ ] Scanning and linking from a phone moves the device to playing (or idle) within
      ~3 s.
- [ ] Leave it unlinked for 15+ minutes: it gets a fresh code by itself.
- [ ] Power-cycle after linking: it comes back linked and playing, without a code.
- [ ] Remove the screen from the dashboard: within one poll it is back on a pairing
      code.
- [ ] "Forget this account" from the device's own menu does the same and the dashboard
      shows it gone.

- [ ] **Try a sample gallery** plays the public sample from the pairing screen, with the
      pairing code still visible in a corner; linking from a phone meanwhile switches to
      the owner's gallery; Back returns to pairing.
- [ ] Every text on the device matches SPEC §11c — nothing mentions buying, prices or
      plans (check the unlicensed screen especially).

## C2. Storage modes (SPEC §11a)

- [ ] Streaming device: on a throttled connection the wall never changes to a piece
      that has not loaded; it rejoins the clock when the next piece arrives.
- [ ] Device with persistent storage: reports `persistentStorage: true`; "Keep this
      gallery on the screen" in the dashboard starts playback at once and then stores
      every piece in order; the dashboard shows "Storing on the screen · n of N", then
      "all N pieces — plays offline".
- [ ] With the gallery stored: pull the network and power-cycle — it plays the whole
      show from storage. Change the gallery: new pieces download, unused ones are
      deleted. Turn the option off: the stored copy is cleared.

## C. Playback

- [ ] Play a gallery: pieces change every `slideSeconds`, with the chosen transition.
- [ ] Pause / resume / next / previous / jump to a piece / restart from the dashboard:
      each reaches the wall within one poll, landing exactly where the dashboard says.
- [ ] Two devices (or device + simulator) **in step**: same piece, same moment, within
      ~150 ms, across 10 minutes. Take one out of step: it carries on alone, nothing
      jumps.
- [ ] A video plays to its end, never cut; a short video loops whole plays; the first
      play of a new video is reported (`durations`) and the dashboard's timeline updates.
- [ ] "Show on a screen" from an artwork page: the piece takes over at once, for the
      chosen time or until dismissed, then the show resumes **in step**.
- [ ] `identify`, `reload`, `clear-cache` from the card's menu each work exactly once.
- [ ] Stop: the device goes to its idle screen (time, "Ready", name, @owner).
- [ ] An account over its licence count: the uncovered screen shows the licence
      notice and plays nothing; freeing a licence brings it back by itself.

## D. Presentation (change each in Exhibition settings; the wall redraws within a poll)

- [ ] `fit` BLUR (wash strength 0 / 60 / 100; shadow on/off), FRAME (all five styles),
      BLACK, FILL — each matches the simulator.
- [ ] `marginPercent` 0 and 20.
- [ ] Label: museum card / one line / none; whole time vs start of each piece; all four
      corners; title/artist/year/description toggles. The label is legible over a white
      piece and a black piece.
- [ ] QR: each target; all four corners; none. Scanning the code on the wall opens the
      right page on a phone.
- [ ] `imageQuality` ORIGINAL on a 4K panel is visibly sharper than DISPLAY.
- [ ] Opening hours: outside them the wall is black (and the panel in standby where the
      platform allows); a takeover still shows.
- [ ] Burn-in guard: the label and QR shift a few pixels every ~2 minutes.

## E. Resilience

- [ ] Pull the network for 5 minutes while playing: the wall keeps playing by the clock;
      reconnect: it re-syncs without a visible jump (unless the clock was moved).
- [ ] Reboot with no network: it plays the last playlist from cache.
- [ ] A piece whose file 404s is skipped for that lap; the dashboard shows the error in
      the screen's state.
- [ ] 8 hours unattended: no memory growth that matters, no crash; if it does crash,
      the watchdog restarts it into the same state.
- [ ] The access token appears in no log, crash report or screenshot.
- [ ] Commercial screen (placed at a venue in Commercial): with the network pulled for
      5 minutes, it keeps playing; on reconnect its first successful poll carries
      `plays` for those minutes, and the venue's report on `/dashboard/commercial` grows by
      about that much time (SPEC §4.1).
- [ ] `state.visible` is `false` outside the opening hours and while the app is in the
      background, and no `plays` are recorded then.

## F. Store / platform

- [ ] Starts on power-on where the platform allows; the screensaver never appears while
      playing.
- [ ] Meets the platform's own certification rules (Roku, Samsung Seller Office, …).
- [ ] The README of the device repo says how to build, install, configure the base URL
      and run the conformance tests.
