import type { ScreenSettings } from './screen-settings';

/**
 * The clock every screen plays by (`0078`).
 *
 * A show is a **timeline** — the pieces in playing order, each with how long it
 * stays up — and an **epoch**, the moment position zero fell. Where a screen is
 * is then arithmetic: `(now − epoch) mod length`. That is the whole of keeping
 * several screens in step: they are given the same timeline and the same epoch,
 * correct their own clock against the server's on every poll, and land on the
 * same piece at the same moment without ever talking to each other.
 *
 * Every control is a change to the clock, not a message to a screen. Pause
 * stops it (`pausedAt`); resume moves the epoch forward by the time spent
 * paused; next and previous move the epoch so that *now* is the start of the
 * piece wanted. A screen that missed a poll is therefore never out of step for
 * longer than one poll — it never has a queue of instructions to replay.
 *
 * A video is never cut off, so its slot has to be its real length. Nothing on
 * the server can measure a file, so the first screen to play a video reports
 * how long it was, and the show is re-timed around it (`reanchor`), keeping
 * whatever is on screen exactly where it is. Until then a video's slot is
 * marked `estimated` and a screen extends it rather than cutting.
 *
 * Pure, and mirrored line for line by the device simulator, because a player
 * that disagreed with this about where it should be is a player out of step.
 */

/** One piece in a playlist, as a screen receives it. */
export interface ScreenItem {
  /** Stable across rebuilds: `a:<artworkId>`, `h:<holdingId>`, `p:<physicalId>`, `g:<galleryItemId>`. */
  id: string;
  kind: 'IMAGE' | 'VIDEO';
  title: string;
  artist: { name: string; handle: string | null } | null;
  /** Year minted or made, when known. */
  year: number | null;
  description: string | null;
  media: {
    /** The original file. For a video, the film. */
    original: string | null;
    /** The 1000px display copy of a still (or of a video's poster). */
    display: string | null;
    /** A small still: the poster under a video, the placeholder while loading. */
    poster: string | null;
    /** Other places the same file can be fetched from when the first fails. */
    fallbacks: string[];
  };
  links: {
    solpersona: string | null;
    vault: string | null;
    marketplace: string | null;
  };
}

export interface TimelineSlot {
  id: string;
  ms: number;
  /** A video whose length no screen has reported yet; the slot is a guess. */
  estimated?: boolean;
}

export interface ClockState {
  epochMs: number;
  pausedAtMs: number | null;
}

export interface Position {
  index: number;
  /** How far into the current piece. */
  offsetMs: number;
  /** How many times the whole timeline has gone round. */
  lap: number;
}

/** The slot a video gets from its length: whole plays only, never cut. */
export function videoSlotMs(durationMs: number, settings: Pick<ScreenSettings, 'slideSeconds' | 'loopShortVideos' | 'maxVideoLoops'>): number {
  const d = Math.max(1, Math.round(durationMs));
  if (!settings.loopShortVideos) return d;
  const plays = Math.min(settings.maxVideoLoops, Math.max(1, Math.ceil((settings.slideSeconds * 1000) / d)));
  return d * plays;
}

export function buildTimeline(
  items: Pick<ScreenItem, 'id' | 'kind' | 'media'>[],
  settings: Pick<ScreenSettings, 'slideSeconds' | 'loopShortVideos' | 'maxVideoLoops'>,
  durationOf: (item: Pick<ScreenItem, 'id' | 'kind' | 'media'>) => number | null,
): TimelineSlot[] {
  const still = settings.slideSeconds * 1000;
  return items.map((item) => {
    if (item.kind !== 'VIDEO') return { id: item.id, ms: still };
    const known = durationOf(item);
    return known ? { id: item.id, ms: videoSlotMs(known, settings) } : { id: item.id, ms: still, estimated: true };
  });
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

/** The epoch that puts the start of `index` at `nowMs` (or at the pause, when paused). */
export function epochForIndex(timeline: TimelineSlot[], index: number, clock: ClockState, nowMs: number): number {
  const n = timeline.length;
  const wrapped = n === 0 ? 0 : ((index % n) + n) % n;
  return (clock.pausedAtMs ?? nowMs) - startOf(timeline, wrapped);
}

/** Pause: the clock stops where it is. A second pause changes nothing. */
export function pause(clock: ClockState, nowMs: number): ClockState {
  return clock.pausedAtMs === null ? { ...clock, pausedAtMs: nowMs } : clock;
}

/** Resume: the epoch moves forward by the time spent paused, so nothing is skipped. */
export function resume(clock: ClockState, nowMs: number): ClockState {
  if (clock.pausedAtMs === null) return clock;
  return { epochMs: clock.epochMs + (nowMs - clock.pausedAtMs), pausedAtMs: null };
}

/** Step `by` pieces (−1 previous, +1 next), landing at the start of that piece. */
export function step(timeline: TimelineSlot[], clock: ClockState, nowMs: number, by: number): ClockState {
  const position = positionAt(timeline, clock, nowMs);
  if (!position) return clock;
  return { ...clock, epochMs: epochForIndex(timeline, position.index + by, clock, nowMs) };
}

/** Jump to a piece by its id. */
export function goTo(timeline: TimelineSlot[], clock: ClockState, nowMs: number, id: string): ClockState {
  const index = timeline.findIndex((slot) => slot.id === id);
  if (index < 0) return clock;
  return { ...clock, epochMs: epochForIndex(timeline, index, clock, nowMs) };
}

/**
 * A new timeline for the same show — a video's real length arrived, a piece
 * was added, the slide time changed — without moving what is on screen: the
 * same piece, the same distance into it. If that piece is gone, the show
 * carries on from the piece that took its place.
 */
export function reanchor(
  before: TimelineSlot[],
  after: TimelineSlot[],
  clock: ClockState,
  nowMs: number,
): ClockState {
  const position = positionAt(before, clock, nowMs);
  if (!position || after.length === 0) return { ...clock, epochMs: clock.pausedAtMs ?? nowMs };
  const id = before[position.index]!.id;
  let index = after.findIndex((slot) => slot.id === id);
  let offset = position.offsetMs;
  if (index < 0) {
    index = Math.min(position.index, after.length - 1);
    offset = 0;
  }
  offset = Math.min(offset, Math.max(0, after[index]!.ms - 1));
  return { ...clock, epochMs: (clock.pausedAtMs ?? nowMs) - startOf(after, index) - offset };
}

export interface PlayedSpan {
  id: string;
  /** Milliseconds this piece was on screen inside the window. */
  ms: number;
  /** Times it came up inside the window (its slot started in [from, to)). */
  starts: number;
}

/**
 * What was on screen between two instants, by the clock (`0082`): every piece
 * the window touched, how long it was up, and whether it started there. This
 * is how time on a commercial screen is counted — from the same arithmetic the
 * screen plays by, so the count and the wall can never disagree.
 *
 * A paused show plays nothing after the pause. `countOngoing` counts the piece
 * already up at `fromMs` as having come up, for the first window a screen is
 * counted in. The walk is bounded, so a timeline of tiny slots cannot run away.
 */
export function playedBetween(
  timeline: TimelineSlot[],
  clock: ClockState,
  fromMs: number,
  toMs: number,
  options: { countOngoing?: boolean } = {},
): PlayedSpan[] {
  const end = clock.pausedAtMs === null ? toMs : Math.min(toMs, clock.pausedAtMs);
  if (end <= fromMs || timeline.length === 0 || timelineLength(timeline) <= 0) return [];
  const position = positionAt(timeline, { epochMs: clock.epochMs, pausedAtMs: null }, fromMs);
  if (!position) return [];

  const spans = new Map<string, PlayedSpan>();
  let cursor = fromMs;
  let index = position.index;
  let offset = position.offsetMs;
  for (let guard = 0; cursor < end && guard < 10_000; guard += 1) {
    const slot = timeline[index]!;
    const take = Math.min(slot.ms - offset, end - cursor);
    if (take > 0) {
      const span = spans.get(slot.id) ?? { id: slot.id, ms: 0, starts: 0 };
      span.ms += take;
      if (offset === 0 || (guard === 0 && options.countOngoing)) span.starts += 1;
      spans.set(slot.id, span);
    }
    cursor += Math.max(take, 0);
    index = (index + 1) % timeline.length;
    offset = 0;
    if (take <= 0 && slot.ms <= 0) cursor += 1;
  }
  return [...spans.values()];
}

/**
 * Where a piece's QR code leads: the chosen target, else the next one along —
 * a piece with no page on TheVault still deserves a code, and its SolPersona
 * page is the honest next best.
 */
export function qrLinkFor(
  item: Pick<ScreenItem, 'links'>,
  target: 'NONE' | 'SOLPERSONA' | 'VAULT' | 'MARKETPLACE',
): string | null {
  if (target === 'NONE') return null;
  const order =
    target === 'VAULT'
      ? [item.links.vault, item.links.solpersona, item.links.marketplace]
      : target === 'MARKETPLACE'
        ? [item.links.marketplace, item.links.solpersona, item.links.vault]
        : [item.links.solpersona, item.links.vault, item.links.marketplace];
  return order.find((link): link is string => Boolean(link)) ?? null;
}

/** A deterministic shuffle, so a show rebuilt with the same seed keeps its order. */
export function shuffled<T>(items: T[], seed: number): T[] {
  const out = [...items];
  let state = seed >>> 0 || 1;
  const next = () => {
    // xorshift32
    state ^= state << 13;
    state >>>= 0;
    state ^= state >>> 17;
    state ^= state << 5;
    state >>>= 0;
    return state / 0x1_0000_0000;
  };
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(next() * (i + 1));
    [out[i], out[j]] = [out[j]!, out[i]!];
  }
  return out;
}
