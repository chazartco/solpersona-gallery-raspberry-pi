import type { AgentLink } from './agent';
import type { ScreenItem, Settings } from './protocol';
import { chooseCandidates, hash } from './rules';

/**
 * Pieces ready to go on the wall — section 5 of the reference simulator.
 *
 * Entries are keyed by item id + the files chosen for it (a quality change is
 * a different entry). Wanted: the piece on screen, the next `prefetch` in
 * timeline order (wrapping), the previous one, and any takeover. Everything
 * else is evicted. Loads run two at a time, most urgent first, and a piece is
 * only "ready" once decoded, so a transition never shows a half-drawn image.
 *
 * Every file comes through the agent: from the SD card when it is stored
 * there, or redirected to its host while the agent's download catches up.
 */

export type EntryStatus = 'queued' | 'loading' | 'ready' | 'error';

export interface Entry {
  key: string;
  id: string;
  item: ScreenItem;
  kind: 'IMAGE' | 'VIDEO';
  candidates: string[];
  status: EntryStatus;
  /** The URL the element shows (the agent's), and the original file it is. */
  src: string | null;
  from: string | null;
  el: HTMLImageElement | HTMLVideoElement | null;
  stillSrc: string | null;
  w: number;
  h: number;
  duration: number | null;
  error: string | null;
  failedAt: number;
  dead: boolean;
  stillFallback: boolean;
}

export interface Planned {
  item: ScreenItem;
  settings: Settings;
  role: 'takeover' | 'current' | 'next' | 'previous';
}

const MAX_LOADING = 2;

export class MemoryCache {
  readonly entries = new Map<string, Entry>();
  /** URLs the agent has on the card: tried first, so a reboot with no network plays. */
  stored = new Set<string>();
  lastError: string | null = null;
  onSettled: () => void = () => {};
  onDuration: (itemId: string, seconds: number) => void = () => {};

  constructor(
    private agent: AgentLink,
    private screen: () => { width: number; originals: boolean },
  ) {}

  candidates(item: ScreenItem, settings: Settings): string[] {
    const s = this.screen();
    return chooseCandidates(item, settings.imageQuality, s.width, s.originals);
  }

  key(item: ScreenItem, settings: Settings): string {
    return item.id + '#' + hash(this.candidates(item, settings).join('\n'));
  }

  get(item: ScreenItem, settings: Settings): Entry | undefined {
    return this.entries.get(this.key(item, settings));
  }

  ensure(item: ScreenItem, settings: Settings): Entry {
    const key = this.key(item, settings);
    let entry = this.entries.get(key);
    if (entry) {
      if (entry.status === 'error' && Date.now() - entry.failedAt > 60000) {
        // A minute later, from the top: a film gets its film tried again, not only its still.
        Object.assign(entry, { status: 'queued', kind: item.kind, stillFallback: false, error: null });
      }
      return entry;
    }
    entry = {
      key, id: item.id, item, kind: item.kind, candidates: this.candidates(item, settings), status: 'queued',
      src: null, from: null, el: null, stillSrc: null, w: 0, h: 0, duration: null, error: null, failedAt: 0,
      dead: false, stillFallback: false,
    };
    this.entries.set(key, entry);
    return entry;
  }

  countReady(): number {
    let n = 0;
    this.entries.forEach((e) => { if (e.status === 'ready') n += 1; });
    return n;
  }

  bufferedAhead(plan: Planned[]): number {
    let n = 0;
    for (const p of plan) {
      if (p.role !== 'next') continue;
      const e = this.get(p.item, p.settings);
      if (e && e.status === 'ready') n += 1;
    }
    return n;
  }

  /** Keep what the plan and the wall need, evict the rest, start loads in plan order. */
  manage(plan: Planned[], onWall: Set<string>): void {
    const keep = new Set<string>(onWall);
    plan.forEach((p) => keep.add(this.ensure(p.item, p.settings).key));
    this.entries.forEach((entry, key) => { if (!keep.has(key)) this.evict(entry); });
    let loading = 0;
    this.entries.forEach((e) => { if (e.status === 'loading') loading += 1; });
    for (let i = 0; i < plan.length && loading < MAX_LOADING; i += 1) {
      const entry = this.get(plan[i]!.item, plan[i]!.settings);
      if (entry && entry.status === 'queued') {
        loading += 1;
        void this.load(entry);
      }
    }
  }

  evict(entry: Entry): void {
    entry.dead = true;
    if (entry.el instanceof HTMLVideoElement) {
      try {
        entry.el.pause();
        entry.el.removeAttribute('src');
        entry.el.load();
      } catch { /* already gone */ }
    }
    if (entry.el && entry.el.parentNode) entry.el.parentNode.removeChild(entry.el);
    entry.el = null;
    this.entries.delete(entry.key);
  }

  /** `clear-cache` / `reload`: everything not on the wall goes (what is on the wall goes when it leaves). */
  clear(keep: Set<string> = new Set()): void {
    this.entries.forEach((entry, key) => { if (!keep.has(key)) this.evict(entry); });
  }

  private ordered(urls: string[]): string[] {
    // Stored copies first: that order is what lets a screen with no network play.
    return [...urls.filter((u) => this.stored.has(u)), ...urls.filter((u) => !this.stored.has(u))];
  }

  private async load(entry: Entry): Promise<void> {
    entry.status = 'loading';
    entry.error = null;
    let ok = entry.kind === 'VIDEO' ? await this.loadVideo(entry) : await this.loadImage(entry, this.ordered(entry.candidates));
    if (entry.dead) return;
    if (!ok && entry.kind === 'VIDEO') {
      // A film that will not load (or play here) still has a still: show it rather than nothing.
      const m = entry.item.media;
      const stills = [m.display, m.poster].filter((u): u is string => !!u);
      if (stills.length) {
        entry.kind = 'IMAGE';
        entry.stillFallback = true;
        ok = await this.loadImage(entry, this.ordered(stills));
        if (entry.dead) return;
      }
    }
    if (ok) {
      entry.status = 'ready';
    } else {
      entry.status = 'error';
      entry.failedAt = Date.now();
      entry.error = 'no copy of this file could be loaded';
      this.lastError = 'could not load ' + entry.id;
    }
    this.onSettled();
  }

  private async loadImage(entry: Entry, urls: string[]): Promise<boolean> {
    for (const url of urls) {
      if (entry.dead) return false;
      const img = new Image();
      img.decoding = 'async';
      img.src = this.agent.mediaUrl(url);
      try {
        await withTimeout(img.decode(), 60000);
      } catch {
        continue;
      }
      if (!img.naturalWidth || !img.naturalHeight) continue;
      Object.assign(entry, { el: img, src: img.src, from: url, w: img.naturalWidth, h: img.naturalHeight });
      if (!entry.stillFallback) {
        // The BLUR wash: the small copy of the same picture when it is on the card
        // already (decoding a 4K original twice is the Pi's most expensive habit);
        // otherwise the piece itself, which is decoded and local by definition.
        const small = entry.item.media.display || entry.item.media.poster;
        entry.stillSrc = small && small !== url && this.stored.has(small) ? this.agent.mediaUrl(small) : null;
      }
      return true;
    }
    return false;
  }

  private async loadVideo(entry: Entry): Promise<boolean> {
    const still = entry.item.media.display || entry.item.media.poster;
    entry.stillSrc = still ? this.agent.mediaUrl(still) : null;
    for (const url of this.ordered(entry.candidates)) {
      if (entry.dead) return false;
      const v = await this.preloadVideo(entry, this.agent.mediaUrl(url));
      if (!v) continue;
      if (entry.dead) {
        v.removeAttribute('src');
        v.load();
        return false;
      }
      Object.assign(entry, {
        el: v, src: v.src, from: url, w: v.videoWidth || 1920, h: v.videoHeight || 1080, duration: v.duration,
      });
      return true;
    }
    return false;
  }

  /** A fresh muted element, loaded far enough to show its first frame; or null. */
  private preloadVideo(entry: Entry, src: string): Promise<HTMLVideoElement | null> {
    return new Promise((resolve) => {
      const v = document.createElement('video');
      v.muted = true;
      v.playsInline = true;
      v.setAttribute('playsinline', '');
      v.setAttribute('muted', '');
      v.preload = 'auto';
      let done = false;
      const finish = (ok: boolean) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        v.onloadedmetadata = v.onloadeddata = v.onerror = null;
        if (!ok) {
          v.removeAttribute('src');
          v.load();
        }
        resolve(ok ? v : null);
      };
      const timer = setTimeout(() => finish(false), 45000);
      v.onloadedmetadata = () => {
        entry.duration = v.duration;
        this.onDuration(entry.id, v.duration);
      };
      v.onloadeddata = () => finish(true);
      v.onerror = () => finish(false);
      v.src = src;
      v.load();
    });
  }
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('timeout')), ms);
    promise.then(
      (value) => { clearTimeout(timer); resolve(value); },
      (error) => { clearTimeout(timer); reject(error); },
    );
  });
}
