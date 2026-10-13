import type { Entry } from './cache';
import type { ScreenItem, Settings } from './protocol';
import { labelParts } from './rules';

/**
 * Two layers, A and B — section 6 of the reference simulator. The front one is
 * on screen; the next piece is built in the other and brought up by the
 * transition (FADE through black, DISSOLVE, CUT). A layer is only ever built
 * from a cache entry that has finished loading.
 */

export interface Target {
  key: string;
  item: ScreenItem;
  settings: Settings;
  takeover: boolean;
  offsetMs: number;
  slotMs?: number;
  estimated: boolean;
  paused: boolean;
  index: number | null;
  lap?: number;
}

export interface Layer {
  el: HTMLElement;
  key: string | null;
  entryKey: string | null;
  item: ScreenItem | null;
  settings: Settings | null;
  target: Target | null;
  entry: Entry | null;
  video: HTMLVideoElement | null;
  playEnded: boolean;
  estimated: boolean;
  clockSig: string | null;
  label: HTMLElement | null;
  qrSlot: HTMLElement | null;
  qrKey: string | null;
  media: HTMLElement | null;
  holdSince: number;
  lastSeek: number;
}

export function el<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string | null): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text); // never innerHTML with show data
  return node;
}

function blank(node: HTMLElement): Layer {
  return {
    el: node, key: null, entryKey: null, item: null, settings: null, target: null, entry: null, video: null,
    playEnded: false, estimated: false, clockSig: null, label: null, qrSlot: null, qrKey: null, media: null,
    holdSince: 0, lastSeek: 0,
  };
}

export class Renderer {
  readonly layers: [Layer, Layer];
  front: 0 | 1 = 0;
  private transitioning: { done: () => void } | null = null;
  soundBlocked = false;
  onVideoEnded: (layer: Layer) => void = () => {};
  qrFor: (key: string) => string | null = () => null;
  clockSig: () => string = () => '';

  constructor(private stage: HTMLElement, a: HTMLElement, b: HTMLElement) {
    this.layers = [blank(a), blank(b)];
  }

  get current(): Layer {
    return this.layers[this.front];
  }

  get busy(): boolean {
    return this.transitioning !== null;
  }

  unit() {
    const w = this.stage.clientWidth || 1;
    const h = this.stage.clientHeight || 1;
    return { w, h, u: Math.min(w, h) / 100 };
  }

  /** Sizes the piece: contained in the margin (and the frame), or covering under FILL. */
  layout(layer: Layer): void {
    if (!layer.media || !layer.settings || !layer.entry) return;
    const s = layer.settings;
    const media = layer.media;
    if (s.fit === 'FILL') {
      media.style.width = '100%';
      media.style.height = '100%';
      return;
    }
    const g = this.unit();
    const margin = g.u * (s.marginPercent || 0);
    const frame = s.fit === 'FRAME' ? 2 * ((s.frameStyle === 'NONE' ? 0 : g.u * 1.5) + g.u * 4) : 0;
    const availW = Math.max(10, g.w - 2 * margin - frame);
    const availH = Math.max(10, g.h - 2 * margin - frame);
    const w = layer.entry.w || 1600;
    const h = layer.entry.h || 1000;
    const scale = Math.min(availW / w, availH / h);
    media.style.width = Math.floor(w * scale) + 'px';
    media.style.height = Math.floor(h * scale) + 'px';
    media.style.objectFit = 'contain';
  }

  layoutAll(): void {
    this.layers.forEach((layer) => this.layout(layer));
  }

  clear(layer: Layer): void {
    if (layer.video) {
      const v = layer.video;
      v.onended = null;
      try {
        v.pause();
        v.muted = true;
      } catch { /* ignore */ }
      if (v.parentNode) v.parentNode.removeChild(v);
    }
    layer.el.textContent = '';
    layer.el.className = 'layer';
    layer.el.style.transition = '';
    layer.el.style.opacity = '0';
    Object.assign(layer, blank(layer.el));
  }

  clearAll(): void {
    this.finishTransition();
    this.layers.forEach((layer) => this.clear(layer));
  }

  /** Entry keys and files on the wall: never evicted, kept through clear-cache. */
  inUse(): { keys: Set<string>; files: string[] } {
    const keys = new Set<string>();
    const files: string[] = [];
    this.layers.forEach((layer) => {
      if (layer.entryKey) keys.add(layer.entryKey);
      if (layer.entry?.from) files.push(layer.entry.from);
    });
    return { keys, files };
  }

  private buildLabel(item: ScreenItem, s: Settings): HTMLElement | null {
    const parts = labelParts(item, s);
    if (!parts) return null;
    if (s.captionStyle === 'MINIMAL') {
      const line = el('div', 'label-min');
      if (parts.title) line.appendChild(el('i', '', parts.title));
      const rest = [parts.artist, parts.year].filter(Boolean).join(', ');
      if (rest) {
        if (parts.title) line.appendChild(el('span', 'sep', '—'));
        line.appendChild(document.createTextNode(rest));
      }
      return line;
    }
    const card = el('div', 'label-card');
    if (parts.title) card.appendChild(el('div', 'label-title', parts.title));
    if (parts.artist) card.appendChild(el('div', 'label-artist', parts.artist));
    if (parts.year) card.appendChild(el('div', 'label-year', parts.year));
    if (parts.description) card.appendChild(el('div', 'label-desc', parts.description));
    return card;
  }

  private corner(layer: Layer, name: string): HTMLElement {
    const existing = layer.el.querySelector<HTMLElement>('.c-' + name);
    if (existing) return existing;
    const node = el('div', 'corner c-' + name);
    layer.el.appendChild(node);
    return node;
  }

  /** Builds `target` into `layer` from a ready cache entry. */
  build(layer: Layer, target: Target, entry: Entry): void {
    this.clear(layer);
    const item = target.item;
    const s = target.settings;
    layer.el.className = 'layer fit-' + s.fit + (s.fit === 'BLUR' && s.pieceShadow === false ? ' no-shadow' : '');

    if (s.fit === 'BLUR') {
      // The wash is blurred beyond recognition of detail, so the 1000 px display
      // copy does as well as a 4K original — and halves what the Pi must decode.
      const washSrc = entry.stillSrc || entry.src;
      if (washSrc) {
        const wash = el('img', 'wash');
        wash.alt = '';
        wash.decoding = 'async';
        wash.style.opacity = String(Math.max(0, Math.min(100, s.washOpacity ?? 60)) / 100);
        // Offline with only the piece itself on the card: wash from the piece.
        wash.onerror = () => {
          if (entry.src && wash.src !== entry.src) wash.src = entry.src;
        };
        wash.src = washSrc;
        layer.el.appendChild(wash);
      }
    }

    let media: HTMLElement;
    if (entry.kind === 'IMAGE') {
      // The preloaded, decoded element itself when it is free; else a twin (the bytes are in memory).
      let img = entry.el instanceof HTMLImageElement && !entry.el.parentNode ? entry.el : null;
      if (!img) {
        img = new Image();
        img.decoding = 'sync';
        img.src = entry.src!;
      }
      img.alt = item.title || '';
      media = img;
    } else {
      let v = entry.el instanceof HTMLVideoElement && !entry.el.parentNode ? entry.el : null;
      if (!v) {
        v = document.createElement('video');
        v.preload = 'auto';
        v.src = entry.src!;
      }
      v.playsInline = true;
      v.setAttribute('playsinline', '');
      v.loop = false; // whole plays are counted against the clock, so looping is done by hand
      v.muted = !s.videoSound || this.soundBlocked;
      v.poster = entry.stillSrc || '';
      layer.video = v;
      media = v;
    }
    media.className = 'piece';
    layer.media = media;

    const box = el('div', 'piece-box');
    if (s.fit === 'FRAME') {
      const moulding = el('div', 'moulding ' + (s.frameStyle || 'GALLERY_WHITE'));
      const mat = el('div', 'mat');
      mat.appendChild(media);
      moulding.appendChild(mat);
      box.appendChild(moulding);
    } else {
      box.appendChild(media);
    }
    layer.el.appendChild(box);

    // Corners: a QR above a label when they share one.
    let qrSlot: HTMLElement | null = null;
    if (s.qrTarget !== 'NONE') {
      qrSlot = el('div', 'qr-card');
      qrSlot.style.display = 'none';
      this.corner(layer, s.qrCorner).appendChild(qrSlot);
    }
    const label = this.buildLabel(item, s);
    if (label) {
      label.classList.add('fade');
      const labelCorner = this.corner(layer, s.captionCorner);
      if (s.captionCorner.startsWith('BOTTOM')) labelCorner.appendChild(label);
      else labelCorner.insertBefore(label, labelCorner.firstChild);
    }

    Object.assign(layer, {
      key: target.key, entryKey: entry.key, item, settings: s, target, entry, label, qrSlot,
      qrKey: s.qrTarget !== 'NONE' ? item.id + '|' + s.qrTarget + (target.takeover ? '|T' : '') : null,
      estimated: !!target.estimated, clockSig: this.clockSig(), playEnded: false, holdSince: 0, lastSeek: 0,
    });
    this.layout(layer);
    this.paintQr();
    this.updateCaption(layer, target);
    if (layer.video) this.startVideo(layer, target);
  }

  /** In step: a screen that joins mid-slot starts the film where the others are. */
  private startVideo(layer: Layer, target: Target): void {
    const v = layer.video!;
    const d = v.duration;
    if (isFinite(d) && d > 0) {
      const at = (target.offsetMs % (d * 1000)) / 1000;
      try {
        v.currentTime = at > d - 0.25 ? 0 : at;
      } catch { /* not seekable yet */ }
    }
    v.onended = () => this.onVideoEnded(layer);
    if (target.paused) {
      v.pause();
      return;
    }
    this.playVideo(v);
  }

  playVideo(v: HTMLVideoElement): void {
    v.play().catch(() => {
      // Chromium is started allowing sound without a gesture; if it is refused anyway, play muted.
      if (!v.muted) {
        v.muted = true;
        this.soundBlocked = true;
        v.play().catch(() => {});
      }
    });
  }

  paintQr(): void {
    this.layers.forEach((layer) => {
      if (!layer.qrSlot || !layer.qrKey || layer.qrSlot.firstChild) return;
      const url = this.qrFor(layer.qrKey);
      if (!url) return;
      const img = el('img');
      img.alt = 'Scan to view this piece';
      img.src = url;
      layer.qrSlot.appendChild(img);
      layer.qrSlot.appendChild(el('span', '', 'Scan to view'));
      layer.qrSlot.style.display = '';
    });
  }

  /** captionTiming START: up for the first ~8 s of a piece, then it fades. */
  updateCaption(layer: Layer, target: Target): void {
    if (!layer.label || !layer.settings) return;
    layer.label.classList.toggle('gone', layer.settings.captionTiming === 'START' && target.offsetMs > 8000);
  }

  finishTransition(): void {
    if (this.transitioning) {
      const done = this.transitioning.done;
      this.transitioning = null;
      done();
    }
  }

  /** Brings `target` up over whatever is on screen, with the show's transition. */
  show(target: Target, entry: Entry): void {
    this.finishTransition();
    const cur = this.current;
    const nextIndex = this.front === 0 ? 1 : 0;
    const next = this.layers[nextIndex];
    this.build(next, target, entry);
    const s = target.settings;
    let mode = s.transition;
    const T = mode === 'CUT' ? 0 : Math.max(0, s.transitionMs || 0);
    if (!cur.key && mode !== 'CUT') mode = 'FADE'; // from nothing: a fade up from black
    next.el.style.zIndex = '2';
    cur.el.style.zIndex = '1';
    this.front = nextIndex;

    const timers: number[] = [];
    const finish = () => {
      timers.forEach(clearTimeout);
      next.el.style.transition = '';
      next.el.style.opacity = '1';
      if (cur !== next) this.clear(cur);
    };
    const handle = { done: finish };
    this.transitioning = handle;
    const end = () => {
      if (this.transitioning === handle) {
        this.transitioning = null;
        finish();
      }
    };

    if (T === 0) {
      end();
      return;
    }
    void next.el.offsetWidth; // commit opacity 0 before animating
    if (mode === 'DISSOLVE') {
      next.el.style.transition = `opacity ${T}ms ease-in-out`;
      next.el.style.opacity = '1';
      timers.push(window.setTimeout(end, T + 30));
    } else if (!cur.key) {
      next.el.style.transition = `opacity ${T}ms ease-out`;
      next.el.style.opacity = '1';
      timers.push(window.setTimeout(end, T + 30));
    } else {
      // FADE through black: down for half, up for half.
      const half = Math.round(T / 2);
      cur.el.style.transition = `opacity ${half}ms ease-in`;
      cur.el.style.opacity = '0';
      timers.push(window.setTimeout(() => {
        next.el.style.transition = `opacity ${half}ms ease-out`;
        next.el.style.opacity = '1';
      }, half));
      timers.push(window.setTimeout(end, T + 30));
    }
  }

  /** Takes the screen to nothing (idle, unlicensed). */
  fadeOut(): void {
    const cur = this.current;
    if (!cur.key || this.transitioning) return;
    cur.el.style.transition = 'opacity 600ms ease';
    cur.el.style.opacity = '0';
    const finish = () => this.clear(cur);
    const handle = { done: finish };
    this.transitioning = handle;
    window.setTimeout(() => {
      if (this.transitioning === handle) {
        this.transitioning = null;
        finish();
      }
    }, 650);
  }
}
