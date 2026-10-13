/**
 * Server time in the page.
 *
 * The agent measures the offset to SolPersona's clock on every poll (SPEC
 * §5.1: server time minus the midpoint of the round trip, median of five) and
 * stamps every message to the page with its best estimate of server time. The
 * page then only has to carry that across a localhost socket, against
 * `performance.now()` — monotonic, so an NTP step on the Pi's wall clock moves
 * nothing on the wall.
 */

/** One estimate of (server − local): server time minus the midpoint of the round trip. */
export function offsetSample(serverTime: number, sentAt: number, receivedAt: number): number {
  return serverTime - (sentAt + receivedAt) / 2;
}

export class PlayerClock {
  private samples: number[] = [];
  private jump: number | null = null;
  /** server − performance.now(); until the agent speaks, the wall clock. */
  offset = Date.now() - performance.now();
  synced = false;

  /** A message from the agent stamped `serverNow`, arriving `at` (performance.now()). */
  add(serverNow: number, at: number = performance.now()): void {
    if (typeof serverNow !== 'number' || !isFinite(serverNow)) return;
    const sample = serverNow - at;
    // A real jump (the agent's first sync after an offline boot) is followed at
    // once — but only when two messages in a row agree on it. One message delivered
    // late (the agent's loop stalled on the SD card) must not move the wall.
    if (this.synced && Math.abs(sample - this.offset) > 1000) {
      const confirmed = this.jump !== null && Math.abs(sample - this.jump) < 250;
      this.jump = sample;
      if (!confirmed) return;
      this.samples = [];
    }
    this.jump = null;
    this.samples.push(sample);
    if (this.samples.length > 5) this.samples.shift();
    const sorted = [...this.samples].sort((a, b) => a - b);
    this.offset = sorted[Math.floor(sorted.length / 2)]!;
    this.synced = true;
  }

  now(): number {
    return performance.now() + this.offset;
  }
}
