import { describe, expect, it } from 'vitest';
import type { AgentState, ScreenItem, Settings, Show } from '../src/protocol';
import { Player } from '../src/player';
import { QrCache } from '../src/qr';

const settings = {
  slideSeconds: 10, order: 'GALLERY', transition: 'CUT', transitionMs: 0, loopShortVideos: true, maxVideoLoops: 3,
  videoSound: false, fit: 'BLUR', washOpacity: 60, pieceShadow: true, frameStyle: 'GALLERY_WHITE', marginPercent: 4,
  imageQuality: 'AUTO', captionStyle: 'LABEL', captionTiming: 'ALWAYS', captionCorner: 'BOTTOM_LEFT', showTitle: true,
  showArtist: true, showYear: true, showDescription: false, qrTarget: 'NONE', qrCorner: 'BOTTOM_RIGHT', burnInGuard: true,
  prefetch: 2, schedule: null,
} as Settings;

const item = (id: string): ScreenItem => ({
  id, kind: 'IMAGE', title: id, artist: null, year: null, description: null,
  media: { original: `https://cdn/${id}.jpg`, display: null, poster: null, fallbacks: [] },
});

const show: Show = {
  id: 's', version: 1, epochMs: 0, pausedAtMs: null, settings,
  items: ['a', 'b', 'c', 'd'].map(item),
  timeline: ['a', 'b', 'c', 'd'].map((id) => ({ id, ms: 10000 })),
};

function state(over: Partial<AgentState> = {}): AgentState {
  return {
    type: 'state', mode: 'player', status: 'PLAYING', device: null, registration: null, pairError: null, showSig: 's',
    takeover: null, serverNow: 0, clockSynced: true, online: true, lastError: null,
    capabilities: { screenWidth: 3840, screenHeight: 2160, storageMb: 1000, video: true, originals: true, persistentStorage: true },
    kept: { on: false, stored: 0, total: 0, error: null }, dark: false, network: null, appVersion: 'dev', debugAllowed: false,
    baseHost: 'solpersona.com', baseUrl: 'https://solpersona.com', ...over,
  };
}

function harness(now: number) {
  const ready = new Set<string>();
  const shown: string[] = [];
  const sent: unknown[] = [];
  const clock = { now: () => now, add: () => {} };
  const agent = { send: (m: unknown) => sent.push(m), mediaUrl: (u: string) => u, qrUrl: () => '' };
  const cache = {
    onSettled: () => {}, onDuration: () => {}, lastError: null as string | null, stored: new Set<string>(),
    manage: () => {}, countReady: () => ready.size, bufferedAhead: () => 0, clear: () => {},
    get: (i: ScreenItem) => (ready.has(i.id) ? { key: i.id, status: 'ready', error: null } : { key: i.id, status: 'loading' }),
  };
  const current: any = { key: null, item: null, target: null, video: null };
  const renderer: any = {
    current, layers: [current, current], busy: false, soundBlocked: false,
    inUse: () => ({ keys: new Set(), files: [] }), fadeOut: () => {}, updateCaption: () => {}, paintQr: () => {}, clearAll: () => {},
    show: (t: any) => { shown.push(t.item.id); Object.assign(current, { key: t.key, item: t.item, target: t }); },
  };
  const player = new Player(agent as any, clock as any, cache as any, renderer, new QrCache(agent as any));
  player.receive(state());
  player.receive({ type: 'show', show });
  return { player, ready, shown, sent, setNow: (n: number) => { now = n; } };
}

describe('the tick', () => {
  it('plays by the clock', () => {
    const h = harness(15000);
    h.ready.add('b');
    h.player.tick();
    expect(h.shown).toEqual(['b']);
  });

  it('never moves to a piece that has not loaded — the current one holds', () => {
    const h = harness(5000);
    h.ready.add('a');
    h.player.tick();
    h.setNow(12000);
    h.player.tick();
    expect(h.shown).toEqual(['a']);
    expect(h.player.holding).toBe('loading the next piece');
    h.ready.add('b');
    h.player.tick();
    expect(h.shown).toEqual(['a', 'b']); // joins the clock where the clock is
  });

  it('plans the current piece, the next `prefetch`, then the previous', () => {
    const h = harness(15000);
    expect(h.player.planned(15000).map((p) => `${p.role}:${p.item.id}`)).toEqual(['current:b', 'next:c', 'next:d', 'previous:a']);
  });

  it('shows a takeover until its time is up, then rejoins the show in step', () => {
    const h = harness(15000);
    h.ready.add('b').add('z');
    const tk = { item: item('z'), settings, startedAt: new Date(10000).toISOString(), until: new Date(20000).toISOString() };
    h.player.receive(state({ takeover: tk }));
    expect(h.shown.at(-1)).toBe('z');
    h.setNow(21000);
    h.ready.add('c');
    h.player.tick();
    expect(h.shown.at(-1)).toBe('c'); // where the clock is now, not where it was
  });

  it('shows nothing when unlicensed', () => {
    const h = harness(15000);
    h.ready.add('b');
    h.player.receive(state({ status: 'UNLICENSED' }));
    expect(h.player.target).toBeNull();
    expect(h.shown).toEqual([]);
  });

  it('goes dark outside opening hours, and reports it', () => {
    const h = harness(new Date(2026, 9, 12, 3, 0).getTime());
    h.ready.add('a').add('b').add('c').add('d');
    h.player.receive({ type: 'show', show: { ...show, settings: { ...settings, schedule: { on: '09:00', off: '22:00' } } } });
    expect(h.player.blackout).toBe(true);
    h.player.report();
    expect((h.sent.at(-1) as any).state.visible).toBe(false);
  });

  it('identify comes from the agent and lasts about six seconds', () => {
    const h = harness(0);
    h.player.receive({ type: 'event', event: 'identify', name: 'Front window', ms: 6000 });
    expect(h.player.identifyName).toBe('Front window');
    expect(h.player.identifyUntil - Date.now()).toBeGreaterThan(5000);
  });
});
