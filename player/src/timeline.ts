import type { ClockState, TimelineSlot } from './protocol';

/**
 * The clock every screen plays by — `reference/screen-timeline.ts`, ported
 * line for line. A show is a timeline and an epoch; where a screen is, is
 * `(now − epoch) mod length`. Screens given the same timeline and epoch land on
 * the same piece at the same moment without ever talking to each other.
 * `test-vectors.json` checks this port in CI.
 */

export interface Position {
  index: number;
  /** How far into the current piece. */
  offsetMs: number;
  /** How many times the whole timeline has gone round. */
  lap: number;
}

export function timelineLength(timeline: TimelineSlot[]): number {
  return timeline.reduce((sum, slot) => sum + slot.ms, 0);
}

/** When slot `index` starts, measured from position zero of one lap. */
export function startOf(timeline: TimelineSlot[], index: number): number {
  let at = 0;
  for (let i = 0; i < index && i < timeline.length; i += 1) at += timeline[i]!.ms;
  return at;
}

/** Where the show is at `nowMs`, by the clock. */
export function positionAt(timeline: TimelineSlot[], clock: ClockState, nowMs: number): Position | null {
  const total = timelineLength(timeline);
  if (timeline.length === 0 || total <= 0) return null;
  const at = (clock.pausedAtMs ?? nowMs) - clock.epochMs;
  const lap = Math.floor(at / total);
  let into = at - lap * total;
  for (let index = 0; index < timeline.length; index += 1) {
    const ms = timeline[index]!.ms;
    if (into < ms) return { index, offsetMs: into, lap };
    into -= ms;
  }
  return { index: timeline.length - 1, offsetMs: timeline[timeline.length - 1]!.ms, lap };
}
