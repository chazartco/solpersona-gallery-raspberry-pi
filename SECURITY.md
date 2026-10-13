# Security

## Reporting a problem

Please report security issues privately, not in a public issue: use GitHub's
**Report a vulnerability** (Security → Advisories) on this repository. Say what
you found, how to reproduce it, and what it could let someone do. We aim to
answer within a few working days and to ship a fixed image and update promptly.

## What the screen holds, and where

* **One credential**: the screen's own access token, received once when it is
  linked. It is stored by the local agent in
  `/var/lib/solpersona-gallery/device.json` (mode 0600, owned by the `gallery`
  user), never in the browser, never in a log, and sent only to the device API
  on the configured base URL. Authenticated requests do not follow redirects,
  so a redirect cannot carry the token to another host.
* **No other secrets** are in this repository or the image. Release signing is
  done in CI with a key held in GitHub Actions secrets; only its public half is
  in the image.

## What listens

* The agent listens on `127.0.0.1:8737` only. Its WebSocket accepts only the
  player's own page (checked by `Origin`).
* While the screen has no network at all and opens its setup Wi-Fi
  (`SolPersona-Gallery-XXXX`), a setup page listens on that network's own
  address (`10.42.0.1:80`), and only for as long as the setup network is open.
  The setup network is open (no password) unless `hotspot_password` is set in
  `solpersona-gallery.toml` — anyone nearby could choose the screen's Wi-Fi
  during those minutes, which is the trade-off for setting up a screen with no
  keyboard.
* SSH is off unless turned on in Raspberry Pi Imager's settings; the image's
  own account is locked.

## Updates

* The app updates itself only from this repository's GitHub releases, and only
  when `SHA256SUMS` carries a valid Ed25519 signature by the key baked into
  the image and the bundle matches it. A new version that does not sync within
  five minutes is rolled back and never tried again.
* Debian security updates are applied weekly in a maintenance window
  (`unattended-upgrades`, security origin only).

## Media

Artwork files come from the public `https://` URLs SolPersona sends and need
no credential. Large originals may be re-encoded at the screen's size with
Pillow, which runs with a decompression-bomb limit; animated, SVG and AVIF
files are never decoded by the agent.
