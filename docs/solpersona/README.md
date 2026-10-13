# SolPersona Gallery — device development kit

Everything a developer, or a coding agent, needs to build a **SolPersona Gallery**
player for a device: a television app, a set-top box channel, a Raspberry Pi image.
A gallery player pairs with a SolPersona account by QR code and shows that account's
galleries on the wall, in step with its other screens, controlled from SolPersona's
dashboard.

This folder is self-contained. Share it as it is: copy it into the device repo as
`docs/solpersona/` and hand the agent the matching prompt.

| | |
|---|---|
| [`SPEC.md`](SPEC.md) | **The contract.** Pairing, the poll, the clock, media, presentation settings, QR codes, commands, device requirements. |
| [`CONFORMANCE.md`](CONFORMANCE.md) | The checklist a player must pass on real hardware before it ships. |
| [`test-vectors.json`](test-vectors.json) | Cases every port must pass in its own CI: the clock, the clock offset, image choice, opening hours. SolPersona's own test suite checks them against the server's clock on every build. |
| [`reference/`](reference/) | The reference player (`device-simulator.html`), the clock (`screen-timeline.ts`) and the settings catalogue (`screen-settings.ts`). |
| [`store-submission/`](store-submission/README.md) | Which devices to target and in what order, what every store needs (privacy policy, reviewer account, neutral purchase wording), and one guide per store: Samsung, LG, Google TV / Android TV, Amazon Fire TV, Roku, Apple TV, Google Cast, Vizio, Raspberry Pi and signage. |
| [`prompts/`](prompts/) | Ready-to-paste build prompts: [`raspberry-pi.md`](prompts/raspberry-pi.md), [`roku.md`](prompts/roku.md), [`samsung-tizen.md`](prompts/samsung-tizen.md), [`brightsign.md`](prompts/brightsign.md), and [`_TEMPLATE.md`](prompts/_TEMPLATE.md) for any other device. |

## Where the code lives — one public repository per device

Recommended, and what the prompts assume:

* **`solpersona-gallery-<device>`**, one repository each (`-raspberry-pi`, `-roku`,
  `-samsung-tizen`, …). Each platform has its own language, toolchain, packaging and
  store, and each releases on its own clock — one repository would bind three
  unrelated build systems together and slow every release to the slowest store review.
* **Public is safe.** A player holds no secrets: its only credential is the per-device
  token it receives at pairing, kept on the device. The device API it talks to is
  already public. Store signing keys are injected from CI secrets and never committed.
  SolPersona's own repository — the server, billing, OAuth — stays private.
* **What keeps three players consistent is this folder**, not shared code: one spec,
  one set of test vectors in every repo's CI, one conformance checklist, and the
  reference simulator to compare against side by side.

## Using a prompt

1. Create the empty repo, copy this folder into it as `docs/solpersona/`.
2. Give the agent the device's prompt from `prompts/`, plus:
   * a development SolPersona URL it can reach and a test account (or staging);
   * the hardware (or tell it what to emulate), and for stores, who owns the developer
     account;
   * the licence for the public repo (the prompts default to proprietary until told).
3. Expect back: the app, CI, a public README, `docs/CONFORMANCE-RESULTS.md` and
   `docs/QUESTIONS.md` (anything the spec did not cover for that platform, and any
   server change the agent proposes rather than invents).

## Keeping this folder current

The originals are in SolPersona: `docs/GALLERY-SCREENS.md` (operator view),
`public/device-simulator.html`, `src/lib/screen-timeline.ts`,
`src/lib/screen-settings.ts`. When the device protocol changes:

1. update the code and `SPEC.md` together (additions only within protocol v1);
2. refresh the copies: `cp public/device-simulator.html docs/gallery-development/reference/ && cp src/lib/screen-timeline.ts src/lib/screen-settings.ts docs/gallery-development/reference/`;
3. add vectors to `test-vectors.json` for any new rule (the unit test
   `tests/unit/gallery-test-vectors.test.ts` keeps the clock vectors honest);
4. tell the device repos — each re-copies the folder.

## Known platform gaps to expect (written into the prompts)

* **Roku** has no blur filter (the wash is approximated), may not decode WebP on every
  model, and does not play WebM. Candidate server additions if needed: a pre-blurred
  wash image and a JPEG display copy.
* **Consumer TVs** (Roku, Samsung) cannot auto-start an app at power-on or power the
  panel off on a schedule; signage editions can. The Pi can do both (HDMI-CEC).
