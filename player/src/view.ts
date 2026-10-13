import type { AgentLink } from './agent';
import type { PlayerClock } from './clock';
import type { Player } from './player';
import { qrDataUrl } from './qr';
import { el } from './renderer';
import { timelineLength } from './timeline';

/**
 * Everything on the wall that is not a piece: the pairing, setup, idle and
 * unlicensed screens (wording exactly as SPEC §11c), the small corner cards,
 * identify, the offline dot, black outside opening hours, the remote's
 * overlay (SPEC §11) and the debug panel.
 */

const $ = (id: string) => document.getElementById(id)!;
const pad2 = (n: number) => String(n).padStart(2, '0');
const hhmm = (d: Date) => `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
const hhmmss = (d: Date) => `${hhmm(d)}:${pad2(d.getSeconds())}`;

function fmtDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !isFinite(ms)) return '—';
  const s = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(s / 60)}:${pad2(s % 60)}`;
}

function esc(value: unknown): string {
  return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

export class View {
  debugOn = false;
  overlayOpen = false;
  private overlayFocus = 0;
  private forgetArmed = 0;
  private overlayIdle = 0;
  private qrCacheText = new Map<string, string>();

  constructor(
    private player: Player,
    private agent: AgentLink,
    private clock: PlayerClock,
    private stage: HTMLElement,
  ) {}

  private qr(text: string): string {
    let url = this.qrCacheText.get(text);
    if (!url) {
      url = qrDataUrl(text);
      if (this.qrCacheText.size > 8) this.qrCacheText.clear();
      this.qrCacheText.set(text, url);
    }
    return url;
  }

  // Layout ---------------------------------------------------------------------------

  applySize(): void {
    const w = window.innerWidth;
    const h = window.innerHeight;
    this.stage.style.setProperty('--u', (Math.min(w, h) / 100).toFixed(3) + 'px');
    this.stage.classList.toggle('portrait', h > w);
    this.player.renderer.layoutAll();
  }

  /** burnInGuard: the label, the code and the idle clock move a few pixels every two minutes. */
  burnInShift(): void {
    const settings = this.player.target?.settings;
    const on = !settings || settings.burnInGuard !== false;
    const range = this.player.renderer.unit().u * 0.6;
    const shift = () => (on ? Math.round((Math.random() * 2 - 1) * range) : 0);
    this.stage.style.setProperty('--bx', shift() + 'px');
    this.stage.style.setProperty('--by', shift() + 'px');
  }

  // Painting ----------------------------------------------------------------------------

  paint(): void {
    const p = this.player;
    const a = p.a;
    const mode = p.mode;
    const now = this.clock.now();
    const hotspot = a?.network?.hotspot ?? null;

    const identifying = p.identifyUntil > Date.now() && mode === 'player';
    if (identifying) $('identifyName').textContent = p.identifyName;
    $('identify').classList.toggle('on', identifying);
    const offline = (mode === 'player' || mode === 'sample') && (!this.agent.connected || (a ? !a.online : false));
    $('offline').classList.toggle('on', offline);

    let screen: string | null = null;
    if (!a) screen = null;
    else if (mode === 'pairing' && hotspot) screen = 'screenSetup';
    else if (mode === 'pairing') screen = 'screenPairing';
    else if (mode === 'player' && a.status === 'UNLICENSED') screen = 'screenUnlicensed';
    else if (mode === 'player' && !p.target && !p.renderer.current.key) screen = 'screenIdle';
    for (const id of ['screenPairing', 'screenSetup', 'screenIdle', 'screenUnlicensed']) $(id).classList.toggle('on', id === screen);

    $('blackout').classList.toggle('on', p.blackout);
    if (p.blackout) {
      const sch = p.show?.settings.schedule;
      $('blackoutClock').textContent = `closed · ${hhmmss(new Date(now))}${sch ? ` · open ${sch.on}–${sch.off}` : ''}`;
    }
    const waiting = (mode === 'sample' || mode === 'player') && !p.renderer.current.key && !!p.target;
    $('loading').classList.toggle('on', waiting || (mode === 'boot' && !a));

    if (screen === 'screenPairing') this.paintPairing(now);
    if (screen === 'screenSetup') this.paintSetup();
    if (screen === 'screenIdle' || screen === 'screenUnlicensed') this.paintIdle(now);
    this.paintSampleCard();
    this.paintSetupCard();
    if (this.overlayOpen) this.paintOverlay();
    if (this.debugOn) this.paintDebug(now);
  }

  private paintPairing(now: number): void {
    const a = this.player.a!;
    const reg = a.registration;
    const img = $('pairQr') as HTMLImageElement;
    const codeEl = $('pairCode');
    $('pairErr').textContent = a.pairError || '';
    if (!reg) {
      img.hidden = true;
      $('pairQrPh').style.display = '';
      if (codeEl.dataset.code !== '') {
        codeEl.dataset.code = '';
        codeEl.textContent = '····';
        codeEl.appendChild(el('span', 'dash', '-'));
        codeEl.appendChild(document.createTextNode('····'));
      }
      $('pairHost').textContent = a.baseHost + '/pair';
      $('pairExpiry').textContent = '';
      $('pairDevice').textContent = '';
      return;
    }
    // The server's QR picture, or one drawn here from pairUrl (SPEC §3.2).
    const src = reg.qrDataUri && reg.qrDataUri.length > 30 ? reg.qrDataUri : this.qr(reg.pairUrl);
    if (img.getAttribute('src') !== src) img.src = src;
    img.hidden = false;
    $('pairQrPh').style.display = 'none';
    if (codeEl.dataset.code !== reg.pairingCode) {
      codeEl.dataset.code = reg.pairingCode;
      const parts = String(reg.pairingCode || '').split('-');
      codeEl.textContent = parts[0] || '';
      if (parts.length > 1) {
        codeEl.appendChild(el('span', 'dash', '-'));
        codeEl.appendChild(document.createTextNode(parts.slice(1).join('-')));
      }
    }
    const pairPage = reg.pairUrl ? reg.pairUrl.replace(/\?.*$/, '') : a.baseUrl + '/pair';
    $('pairHost').textContent = pairPage.replace(/^https?:\/\//, '');
    const left = reg.expiresAt ? reg.expiresAt - Date.now() : null;
    $('pairExpiry').textContent = left === null ? '' : left > 0 ? `A new code in ${fmtDuration(left)}` : 'Getting a new code…';
    $('pairDevice').textContent = 'Screen ' + reg.deviceId;
    void now;
  }

  private paintSetup(): void {
    const net = this.player.a!.network!;
    const hs = net.hotspot!;
    const img = $('setupQr') as HTMLImageElement;
    const src = this.qr(hs.qr);
    if (img.getAttribute('src') !== src) img.src = src;
    $('setupSsid').textContent = hs.ssid;
    ($('setupSecured') as HTMLElement).hidden = !hs.secured;
    $('setupErr').textContent = net.error || '';
  }

  private paintIdle(now: number): void {
    const a = this.player.a!;
    const info = a.device;
    $('idleClock').textContent = hhmm(new Date(now));
    $('idleReady').textContent = a.status === null ? 'Connecting' : 'Ready';
    $('idleName').textContent = info?.name || '';
    $('idleOwner').textContent = info?.owner ? '@' + info.owner : '';
    $('inactiveName').textContent = info ? info.name + (info.owner ? ' · @' + info.owner : '') : '';
    $('inactiveHost').textContent = a.baseHost;
  }

  /** A corner the show leaves free (not the label's, not the code's). */
  private freeCorner(): string {
    const s = this.player.show?.settings;
    const used = s ? [s.captionStyle !== 'NONE' ? s.captionCorner : null, s.qrTarget !== 'NONE' ? s.qrCorner : null] : [];
    return ['TOP_RIGHT', 'TOP_LEFT', 'BOTTOM_RIGHT', 'BOTTOM_LEFT'].find((c) => !used.includes(c as never)) || 'TOP_RIGHT';
  }

  /** "Link this screen — solpersona.com/pair · ABCD-EFGH" while the sample plays (SPEC §11b). */
  private paintSampleCard(): void {
    const card = $('sampleCard');
    const a = this.player.a;
    if (this.player.mode !== 'sample' || !a) {
      card.className = 'card';
      return;
    }
    const reg = a.registration;
    const pairPage = reg?.pairUrl ? reg.pairUrl.replace(/\?.*$/, '') : a.baseUrl + '/pair';
    $('sampleHost').textContent = pairPage.replace(/^https?:\/\//, '');
    $('sampleCode').textContent = reg?.pairingCode || '';
    $('sampleCodeSep').style.display = reg ? '' : 'none';
    const img = $('sampleQr') as HTMLImageElement;
    if (reg) {
      const src = reg.qrDataUri && reg.qrDataUri.length > 30 ? reg.qrDataUri : this.qr(reg.pairUrl);
      if (img.getAttribute('src') !== src) img.src = src;
      img.hidden = false;
    } else img.hidden = true;
    const wanted = 'card on c-' + this.freeCorner();
    if (card.className !== wanted) card.className = wanted;
  }

  /** Playing from the card with no network, and the setup network is open. */
  private paintSetupCard(): void {
    const card = $('setupCard');
    const hs = this.player.a?.network?.hotspot;
    if (!hs || this.player.mode !== 'player' || this.player.blackout) {
      card.className = 'card';
      return;
    }
    const img = $('setupCardQr') as HTMLImageElement;
    const src = this.qr(hs.qr);
    if (img.getAttribute('src') !== src) img.src = src;
    $('setupCardSsid').textContent = hs.ssid;
    const wanted = 'card on c-' + this.freeCorner();
    if (card.className !== wanted) card.className = wanted;
  }

  // The overlay (OK / Enter) ---------------------------------------------------------------

  openOverlay(open: boolean): void {
    this.overlayOpen = open;
    this.overlayFocus = 0;
    this.forgetArmed = 0;
    this.overlayIdle = Date.now();
    $('overlay').classList.toggle('on', open);
    if (open) this.paintOverlay();
  }

  overlayKey(key: string): void {
    this.overlayIdle = Date.now();
    if (key === 'ArrowLeft' || key === 'ArrowUp') this.overlayFocus = 0;
    else if (key === 'ArrowRight' || key === 'ArrowDown') this.overlayFocus = 1;
    else if (key === 'Enter') {
      if (this.overlayFocus === 0) {
        if (this.forgetArmed && Date.now() - this.forgetArmed < 8000) {
          this.agent.send({ type: 'action', action: 'forget' });
          this.openOverlay(false);
          return;
        }
        this.forgetArmed = Date.now();
      } else {
        this.setDebug(!this.debugOn);
      }
    }
    this.paintOverlay();
  }

  private paintOverlay(): void {
    if (Date.now() - this.overlayIdle > 30000) {
      this.openOverlay(false);
      return;
    }
    const a = this.player.a;
    const net = a?.network;
    $('ovName').textContent = a?.device?.name || 'This screen';
    const rows: [string, string][] = [
      ['Owner', a?.device?.owner ? '@' + a.device.owner : '—'],
      ['Connection', !a ? 'starting…' : a.online ? 'connected to ' + a.baseHost : 'offline — playing from this screen'],
      ['Network', net ? (net.kind === 'ethernet' ? 'Ethernet' : net.kind === 'wifi' ? 'Wi-Fi ' + (net.ssid || '') : net.hotspot ? 'setup network ' + net.hotspot.ssid : 'none') + (net.ip ? ' · ' + net.ip : '') : '—'],
      ['Stored', a && a.kept.total ? `${a.kept.stored} of ${a.kept.total} pieces` : '—'],
      ['Version', a?.appVersion || '—'],
    ];
    const box = $('ovRows');
    box.textContent = '';
    rows.forEach(([k, v]) => {
      box.appendChild(el('span', 'k', k));
      box.appendChild(el('span', '', v));
    });
    const forget = $('ovForget');
    forget.textContent = this.forgetArmed && Date.now() - this.forgetArmed < 8000 ? 'Press OK again to forget' : 'Forget this account';
    forget.className = 'ov-btn danger' + (this.overlayFocus === 0 ? ' focus' : '');
    const debug = $('ovDebug');
    debug.textContent = this.debugOn ? 'Hide debug' : 'Show debug';
    debug.className = 'ov-btn' + (this.overlayFocus === 1 ? ' focus' : '');
  }

  // Debug --------------------------------------------------------------------------------------

  setDebug(on: boolean): void {
    this.debugOn = on;
    document.body.classList.toggle('debug', on);
    this.agent.send({ type: 'debug', on });
    if (on) this.paintDebug(this.clock.now());
  }

  private paintDebug(now: number): void {
    const p = this.player;
    const a = p.a;
    const d = a?.debug;
    const show = p.show;
    const t = p.target;
    const cur = p.renderer.current;
    const kv = (rows: [string, unknown, boolean?][]) =>
      '<div class="kv">' + rows.map((r) => `<span>${esc(r[0])}</span><span title="${esc(r[1])}">${r[2] ? r[1] : esc(r[1])}</span>`).join('') + '</div>';
    let html = '<h3>Connection</h3>' + kv([
      ['mode', p.mode + (p.mode === 'sample' ? ' (public sample)' : '')],
      ['status', a?.status || '—'],
      ['agent', this.agent.connected ? '<span class="ok">connected</span>' : '<span class="bad">not connected</span>', true],
      ['online', a?.online ? '<span class="ok">yes</span>' : `<span class="bad">no (${esc(d?.failures ?? '?')} failures)</span>`, true],
      ['base', a?.baseUrl || '—'],
      ['device', d?.deviceId || a?.device?.id || '—'],
      ['name / owner', a?.device ? `${a.device.name} / @${a.device.owner || '—'}` : '—'],
      ['clock offset', d?.offset != null ? `${Math.round(d.offset)} ms (rtt ${Math.round(d.rtt ?? 0)} ms)` : 'not synced'],
      ['server now', hhmmss(new Date(now))],
      ['last sync', d?.lastSyncAgoMs != null ? (d.lastSyncAgoMs / 1000).toFixed(1) + ' s ago' : '—'],
      ['next sync', d?.nextSyncInMs != null ? (d.nextSyncInMs / 1000).toFixed(1) + ' s' : '—'],
      ['screen', a ? `${a.capabilities.screenWidth}×${a.capabilities.screenHeight}${a.capabilities.originals ? ' · originals' : ''} · window ${window.innerWidth}×${window.innerHeight}` : '—'],
      ['panel power', d?.power || '—'],
      ['error', a?.lastError || p.cache.lastError || '—'],
    ]);
    if (show) {
      html += '<h3>Show</h3>' + kv([
        ['id', show.id],
        ['version', show.version],
        ['source', show.sourceRef || '—'],
        ['epoch', new Date(show.epochMs).toISOString()],
        ['paused', show.pausedAtMs !== null ? 'at ' + new Date(show.pausedAtMs).toISOString() : 'no'],
        ['pieces', `${show.items.length} · lap ${fmtDuration(timelineLength(show.timeline))}`],
        ['fit / transition', `${show.settings.fit} / ${show.settings.transition} ${show.settings.transitionMs}ms`],
        ['schedule', show.settings.schedule ? `${show.settings.schedule.on}–${show.settings.schedule.off}${p.blackout ? ' (closed)' : ''}` : 'always'],
      ]);
    }
    if (t) {
      const pct = t.slotMs ? Math.min(100, (t.offsetMs / t.slotMs) * 100) : 0;
      const v = cur.video;
      html += `<h3>${t.takeover ? 'Takeover' : 'Position'}</h3>` + kv([
        ['index', t.index === null ? '—' : `${t.index}${t.lap !== undefined ? ` (lap ${t.lap})` : ''}`],
        ['item', t.item.id],
        ['title', t.item.title || '—'],
        ['kind', t.item.kind + (t.estimated ? ' · estimated slot' : '')],
        ['offset / slot', `${fmtDuration(t.offsetMs)} / ${t.slotMs ? fmtDuration(t.slotMs) : '∞'}`],
        ['on screen', cur.item ? cur.item.id + (cur.key === t.key ? '' : ' (held)') : '—'],
        ['holding', p.holding ? `<span class="warn">${esc(p.holding)}</span>` : '—', true],
        ['video', v ? `${v.paused ? 'paused' : 'playing'} ${v.currentTime.toFixed(1)} / ${isFinite(v.duration) ? v.duration.toFixed(1) : '?'} s${v.muted ? ' · muted' : ' · sound'}` : '—'],
      ]);
      if (t.slotMs) html += `<div class="bar"><i style="width:${pct.toFixed(1)}%"></i></div>`;
    }
    const entries = [...p.cache.entries.values()];
    html += `<h3>Memory (${entries.length}, ${p.cache.countReady()} ready)</h3>`;
    html += entries.length
      ? kv(entries.map((e) => {
          const cls = e.status === 'ready' ? 'ok' : e.status === 'error' ? 'bad' : 'warn';
          const stored = e.from && p.cache.stored.has(e.from) ? 'card' : 'net';
          const detail = e.status === 'ready' ? `${stored} ${e.w}×${e.h}${e.stillFallback ? ' still' : ''}` : e.status;
          return [e.id, `<span class="${cls}">${esc(detail)}</span>`, true] as [string, string, boolean];
        }))
      : '<div>—</div>';
    if (a && p.mode === 'player') {
      const k = a.kept;
      const pctK = k.total ? (k.stored / k.total) * 100 : 0;
      html += `<h3>On the card · ${k.stored} of ${k.total}</h3><div class="bar"><i style="width:${pctK.toFixed(1)}%"></i></div>`;
      html += kv([
        ['keepOffline', k.on ? 'on (never evicted)' : 'off (kept while there is room)'],
        ['size', d ? `${d.store.mb} MB of ${d.store.capMb} MB${d.store.roomFull ? ' · full' : ''}` : '—'],
        ['downloading', d ? d.store.running.join(', ') || '—' : '—'],
        ['states', d ? Object.entries(d.store.states).map(([s, n]) => `${s} ${n}`).join(' · ') || '—' : '—'],
        ['error', k.error ? `<span class="warn">${esc(k.error)}</span>` : '—', true],
      ]);
    }
    if (d) {
      html += `<h3>Commands · ackSeq ${esc(d.ackSeq)}</h3>`;
      html += d.commands.length ? kv(d.commands.map((c) => ['#' + c.seq, `${c.type} at ${hhmmss(new Date(c.at))}`])) : '<div>—</div>';
      const pending = Object.entries(d.pendingDurations);
      if (pending.length) html += '<h3>Durations to report</h3>' + kv(pending.map(([id, ms]) => [id, fmtDuration(ms)]));
      if (d.plays) html += kv([['offline plays', String(d.plays)]]);
      html += '<h3>API</h3><div class="log">' + d.calls.slice(0, 30).map((c) => {
        const cls = c.status === 'ERR' || (typeof c.status === 'number' && c.status >= 400) ? 'bad' : 'ok';
        return `<div>${esc(hhmmss(new Date(c.at)))} <span class="${cls}">${esc(c.status)}</span> ${esc(c.method)} ${esc(c.path)} ${esc(c.ms)}ms ${esc(c.note)}</div>`;
      }).join('') + '</div>';
    }
    $('debug').innerHTML = html;
  }
}
