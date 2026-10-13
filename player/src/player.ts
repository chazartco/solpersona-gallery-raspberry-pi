import type { AgentLink } from './agent';
import type { MemoryCache, Planned } from './cache';
import type { PlayerClock } from './clock';
import type { AgentMessage, AgentState, ScreenItem, Show, Takeover } from './protocol';
import { QrCache } from './qr';
import type { Layer, Renderer, Target } from './renderer';
import { minutesOf, scheduleOpen, settingsSig } from './rules';
import { positionAt } from './timeline';

/**
 * What should be on the wall now — section 7 of the reference simulator.
 *
 * Runs every 250 ms (and whenever something arrives) and recomputes the
 * position from the clock every time: nothing is scheduled, so pause, next,
 * goto and a new epoch are all simply a different answer on the next tick.
 */

export type HeldShow = Show & { byId: Map<string, ScreenItem> };

export class Player {
  a: AgentState | null = null;
  show: HeldShow | null = null;
  target: Target | null = null;
  holding: string | null = null;
  blackout = false;
  identifyUntil = 0;
  identifyName = '';
  private reportedDurations = new Set<string>();
  private lastMode: string | null = null;
  onKey: (key: string) => void = () => {};
  onChange: () => void = () => {};

  constructor(
    private agent: AgentLink,
    private clock: PlayerClock,
    readonly cache: MemoryCache,
    readonly renderer: Renderer,
    readonly qr: QrCache,
  ) {
    cache.onSettled = () => this.tick();
    cache.onDuration = (id, seconds) => this.reportDuration(id, seconds);
    qr.onReady = () => renderer.paintQr();
    renderer.qrFor = (key) => qr.urlFor(key);
    renderer.clockSig = () => this.clockSig();
    renderer.onVideoEnded = (layer) => this.onVideoEnded(layer);
  }

  get mode() {
    return this.a?.mode ?? 'boot';
  }

  get sample() {
    return this.mode === 'sample';
  }

  // From the agent -----------------------------------------------------------------

  receive(m: AgentMessage): void {
    if (m.type === 'state') {
      this.clock.add(m.serverNow);
      const before = this.lastMode;
      this.a = m;
      this.lastMode = m.mode;
      // Leaving the sample or the player for pairing: nothing of it stays on the wall.
      if (before && before !== m.mode && (m.mode === 'pairing' || before === 'sample')) this.dropEverything();
    } else if (m.type === 'show') {
      const prev = this.show;
      this.show = m.show ? { ...m.show, byId: new Map(m.show.items.map((i) => [i.id, i])) } : null;
      if (prev && this.show && prev.settings.qrTarget !== this.show.settings.qrTarget) this.qr.clear();
    } else if (m.type === 'stored') {
      this.cache.stored = new Set(m.urls);
    } else if (m.type === 'event') {
      if (m.event === 'identify') {
        this.identifyUntil = Date.now() + (m.ms ?? 6000);
        this.identifyName = m.name || 'This screen';
      } else if (m.event === 'reload') {
        this.dropEverything();
      } else if (m.event === 'clear-cache') {
        // What is on the wall is decoded already and stays; everything else is fetched again.
        this.cache.clear(this.renderer.inUse().keys);
        this.cache.lastError = null;
      }
    } else if (m.type === 'key') {
      this.onKey(m.key);
    }
    this.tick();
    this.onChange();
  }

  private dropEverything(): void {
    this.renderer.clearAll();
    this.cache.clear();
    this.qr.clear();
    this.target = null;
  }

  // The clock --------------------------------------------------------------------------

  clockSig(): string {
    const s = this.show;
    return s ? `${s.id}|${s.epochMs}|${s.pausedAtMs}` : '';
  }

  /** The takeover, unless its `until` has passed by our clock. */
  activeTakeover(now: number): Takeover | null {
    const tk = this.a?.takeover;
    if (!tk || !tk.item || this.mode !== 'player') return null;
    if (tk.until && Date.parse(tk.until) <= now) return null;
    return tk;
  }

  computeTarget(now: number): Target | null {
    const status = this.a?.status;
    const tk = this.activeTakeover(now);
    if (tk && status !== 'UNLICENSED') {
      return {
        key: 'T|' + tk.item.id + '|' + tk.startedAt + '|' + settingsSig(tk.settings),
        item: tk.item, settings: tk.settings, takeover: true,
        offsetMs: Math.max(0, now - Date.parse(tk.startedAt)), paused: false, estimated: false, index: null,
      };
    }
    const show = this.show;
    if (!show || !show.timeline.length || status === 'UNLICENSED') return null;
    if (this.mode !== 'player' && this.mode !== 'sample') return null;
    const pos = positionAt(show.timeline, show, now);
    if (!pos) return null;
    const slot = show.timeline[pos.index]!;
    const item = show.byId.get(slot.id);
    if (!item) return null;
    return {
      // Keyed by the piece, not the index: a re-timed playlist that keeps the
      // same piece on the wall must not transition to itself.
      key: 'S|' + item.id + '|' + settingsSig(show.settings),
      item, settings: show.settings, takeover: false,
      offsetMs: pos.offsetMs, slotMs: slot.ms, estimated: !!slot.estimated,
      paused: show.pausedAtMs !== null && show.pausedAtMs !== undefined, index: pos.index, lap: pos.lap,
    };
  }

  /** What should be held, most urgent first. */
  planned(now: number): Planned[] {
    const out: Planned[] = [];
    const tk = this.activeTakeover(now);
    if (tk) out.push({ item: tk.item, settings: tk.settings, role: 'takeover' });
    const show = this.show;
    if (show && show.timeline.length && this.a?.status !== 'UNLICENSED' && (this.mode === 'player' || this.mode === 'sample')) {
      const pos = positionAt(show.timeline, show, now);
      if (pos) {
        const n = show.timeline.length;
        const push = (index: number, role: Planned['role']) => {
          const item = show.byId.get(show.timeline[index]!.id);
          if (item && !out.some((p) => p.item.id === item.id && p.settings === show.settings)) out.push({ item, settings: show.settings, role });
        };
        push(pos.index, 'current');
        const ahead = Math.min(show.settings.prefetch || 2, n - 1);
        for (let i = 1; i <= ahead; i += 1) push((pos.index + i) % n, 'next');
        if (n > 1) push((pos.index - 1 + n) % n, 'previous');
      }
    }
    return out;
  }

  /**
   * Videos are never cut off: when an *estimated* slot ends mid-play the film
   * finishes first — unless the clock was moved (pause, next, a new show),
   * which a person asked for and is followed at once. Held at most a play + 5 s.
   */
  private shouldHoldVideo(cur: Layer, target: Target, now: number): boolean {
    if (!cur.video || cur.playEnded || !cur.target || cur.target.takeover || target.takeover) return false;
    if (!cur.estimated || cur.clockSig !== this.clockSig()) return false;
    if (!cur.holdSince) cur.holdSince = now;
    const d = cur.video.duration;
    const limit = (isFinite(d) && d > 0 ? d * 1000 : 60000) + 5000;
    return now - cur.holdSince < limit;
  }

  /** Keeps a playing film where the clock says it should be (screens in step). */
  private syncPlayback(layer: Layer, target: Target, now: number): void {
    layer.target = target;
    layer.estimated = !!target.estimated;
    layer.holdSince = 0;
    const v = layer.video;
    if (!v) return;
    if (target.paused || this.blackout) {
      if (!v.paused) v.pause();
      return;
    }
    if (v.paused && !layer.playEnded && v.readyState >= 2) this.renderer.playVideo(v);
    const d = v.duration;
    if (!target.estimated && isFinite(d) && d > 1 && now - layer.lastSeek > 8000 && !v.paused) {
      const expected = (target.offsetMs % (d * 1000)) / 1000;
      if (expected > 0.6 && expected < d - 0.6 && Math.abs(v.currentTime - expected) > 1) {
        v.currentTime = expected;
        layer.lastSeek = now;
      }
    }
    if (layer.settings && !layer.settings.videoSound !== v.muted && !this.renderer.soundBlocked) v.muted = !layer.settings.videoSound;
  }

  private onVideoEnded(layer: Layer): void {
    if (this.renderer.current !== layer || !layer.video) return;
    const t = this.target;
    if (t && t.key === layer.key && !t.paused && !this.blackout) {
      // The slot is not over: another whole play.
      layer.video.currentTime = 0;
      this.renderer.playVideo(layer.video);
    } else {
      // The slot is over (or we held it for this): now the next piece may come.
      layer.playEnded = true;
      this.tick();
    }
  }

  private reportDuration(itemId: string, seconds: number): void {
    const show = this.show;
    if (!isFinite(seconds) || seconds <= 0 || this.mode !== 'player') return;
    const key = (show ? `${show.id}@${show.version}` : 'takeover') + '|' + itemId;
    if (this.reportedDurations.has(key)) return;
    this.reportedDurations.add(key);
    this.agent.send({ type: 'duration', itemId, ms: Math.round(seconds * 1000) });
  }

  // The tick ------------------------------------------------------------------------------

  tick(): void {
    const playing = this.mode === 'player' || this.sample;
    if (!playing) {
      this.target = null;
      return;
    }
    const now = this.clock.now();
    const target = this.computeTarget(now);
    this.target = target;

    // Opening hours (the show's). A takeover is somebody asking for this piece
    // now, so it is shown whatever the hour.
    const schedule = this.show?.settings.schedule ?? null;
    this.blackout = this.mode === 'player' && !(target && target.takeover) && !scheduleOpen(schedule, minutesOf(new Date(now)));

    const plan = this.planned(now);
    this.cache.manage(plan, this.renderer.inUse().keys);
    plan.slice(0, 2).forEach((p) => this.qr.request(p.item.id, p.settings.qrTarget, p.role === 'takeover', this.sample));
    const keepQr = new Set(plan.map((p) => QrCache.key(p.item.id, p.settings.qrTarget, p.role === 'takeover')));
    this.renderer.layers.forEach((l) => { if (l.qrKey) keepQr.add(l.qrKey); });
    this.qr.prune(keepQr);

    const cur = this.renderer.current;
    if (!target) {
      this.holding = null;
      this.renderer.fadeOut();
      return;
    }
    if (cur.key === target.key) {
      this.syncPlayback(cur, target, now);
      this.renderer.updateCaption(cur, target);
      this.holding = null;
      if (cur.video && this.blackout && !cur.video.paused) cur.video.pause();
      return;
    }
    if (this.renderer.busy) return;
    if (cur.key && this.shouldHoldVideo(cur, target, now)) {
      this.holding = 'letting the video finish';
      return;
    }
    // Never transition to a piece that has not loaded: hold the current one.
    const entry = this.cache.get(target.item, target.settings);
    if (!entry || entry.status !== 'ready') {
      this.holding = entry && entry.status === 'error' ? 'next piece failed to load' : 'loading the next piece';
      return;
    }
    this.holding = null;
    if (!entry.error && this.cache.lastError?.startsWith('could not load')) this.cache.lastError = null;
    this.renderer.show(target, entry);
  }

  /** `state` for the dashboard, about once a second. */
  report(): void {
    const cur = this.renderer.current;
    const now = this.clock.now();
    this.agent.send({
      type: 'report',
      state: {
        itemId: cur.item ? cur.item.id : null,
        index: cur.target && typeof cur.target.index === 'number' ? cur.target.index : null,
        paused: !!this.target?.paused,
        cached: this.cache.countReady(),
        bufferedAhead: this.cache.bufferedAhead(this.planned(now)),
        error: this.cache.lastError,
        visible: !this.blackout && document.visibilityState !== 'hidden' && !!cur.item,
      },
      inUse: this.renderer.inUse().files,
    });
  }
}
