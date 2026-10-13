# Reference files

Copies, for anybody building a player without access to SolPersona's code. **The
originals live in the SolPersona repository and win if they differ**; refresh these
copies whenever the protocol changes (`npm run`-free: plain `cp`).

| File | Original | What it is |
|---|---|---|
| `device-simulator.html` | `public/device-simulator.html` | The reference player: one self-contained HTML page that pairs, polls, plays by the clock, caches, draws every presentation setting and carries out commands. Its script is in sections (api · clock · sync · cache · renderer · tick · pairing · chrome) meant to be ported piece by piece. Open it in a browser — it talks to any SolPersona — and press **D** for the debug panel. |
| `screen-timeline.ts` | `src/lib/screen-timeline.ts` | The clock, in ~150 lines of pure TypeScript. `positionAt` is the function every player must reproduce; the rest (pause, step, reanchor) is what the server does to the clock, included so you can see why the numbers change. |
| `screen-settings.ts` | `src/lib/screen-settings.ts` | Every presentation setting, its range and its default, plus the labels the dashboard shows people. |

`../test-vectors.json` is checked against `screen-timeline.ts` by SolPersona's own test
suite on every build, so a port that passes the vectors agrees with the server.
