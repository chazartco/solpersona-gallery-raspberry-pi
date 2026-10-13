import './styles.css';
import { AgentLink } from './agent';
import { MemoryCache } from './cache';
import { PlayerClock } from './clock';
import { Player } from './player';
import { QrCache } from './qr';
import { Renderer } from './renderer';
import { View } from './view';

/*
 * SolPersona Gallery for Raspberry Pi — the player.
 *
 *   agent.ts     the line to the local agent (state in, reports out)
 *   clock.ts     server time, carried across from the agent
 *   timeline.ts  the clock's arithmetic (SPEC §5), checked by test-vectors.json
 *   cache.ts     pieces decoded and ready, two loads at a time
 *   renderer.ts  two layers, every fit, frame, label, QR, transitions
 *   player.ts    the tick: what should be on the wall now
 *   view.ts      pairing, setup, idle, notices, overlay, debug
 */

const BUILD_VERSION: string = import.meta.env.VITE_APP_VERSION || 'dev';

const $ = (id: string) => document.getElementById(id)!;
const stage = $('stage');
const agent = new AgentLink();
const clock = new PlayerClock();
const renderer = new Renderer(stage, $('layerA'), $('layerB'));
// §6.2 decides by the panel's real width (from the agent, read from EDID), not the window's.
const screenInfo = (): { width: number; originals: boolean } => ({
  width: player.a?.capabilities.screenWidth ?? window.innerWidth * (window.devicePixelRatio || 1),
  originals: player.a?.capabilities.originals ?? true,
});
const cache: MemoryCache = new MemoryCache(agent, screenInfo);
const qr = new QrCache(agent);
const player: Player = new Player(agent, clock, cache, renderer, qr);
const view = new View(player, agent, clock, stage);

/** Keys from a keyboard, or the TV's remote over HDMI-CEC (forwarded by the agent). */
function handleKey(key: string): void {
  const mode = player.mode;
  if (view.overlayOpen) {
    if (key === 'Escape' || key === 'Backspace' || key === 'BrowserBack' || key === 'GoBack') view.overlayBack();
    else view.overlayKey(key);
    view.paint();
    return;
  }
  if (key === 'Escape' || key === 'Backspace' || key === 'BrowserBack' || key === 'GoBack') {
    if (mode === 'sample') agent.send({ type: 'action', action: 'stop-sample' });
    return;
  }
  if (key === 'Enter' || key === 'Select') {
    if (mode === 'pairing' && !player.a?.network?.hotspot) agent.send({ type: 'action', action: 'sample' });
    else if (mode === 'player') view.openOverlay(true);
    return;
  }
  const k = key.toLowerCase();
  if (k === 'd' && (player.a?.debugAllowed || view.debugOn)) view.setDebug(!view.debugOn);
  else if (k === 't' && mode === 'pairing') agent.send({ type: 'action', action: 'sample' });
}

player.onKey = handleKey;
player.onChange = () => view.paint();

document.addEventListener('keydown', (event) => {
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  if ((event.target as HTMLElement | null)?.tagName === 'INPUT') {
    // Typing the Wi-Fi password: letters (and Backspace) go into the field.
    view.touchOverlay();
    if (event.key === 'Enter' || event.key === 'Escape') {
      event.preventDefault();
      handleKey(event.key);
    }
    return;
  }
  event.preventDefault();
  handleKey(event.key);
});

let debugOffered = false;
agent.on((m) => {
  player.receive(m);
  if (m.type === 'state') {
    if (m.debugAllowed && !debugOffered) view.setDebug(true); // `debug = true` in the settings file: shown at start; D hides it
    debugOffered = true;
    reloadIfUpdated(m.appVersion);
  }
});
agent.onOpen(() => {
  if (view.debugOn) agent.send({ type: 'debug', on: true });
  player.report();
});

/** A new release went live under us (the updater restarts the kiosk too; this covers the gap). */
function reloadIfUpdated(agentVersion: string): void {
  if (BUILD_VERSION === 'dev' || !agentVersion || agentVersion === BUILD_VERSION) return;
  try {
    const last = Number(sessionStorage.getItem('reloaded-for-update') || 0);
    if (Date.now() - last < 600000) return;
    sessionStorage.setItem('reloaded-for-update', String(Date.now()));
  } catch { /* storage refused: reload anyway, once per page */ }
  location.reload();
}

window.addEventListener('resize', () => view.applySize());
document.addEventListener('visibilitychange', () => player.tick());
view.applySize();
agent.connect();

setInterval(() => player.tick(), 250);
setInterval(() => view.paint(), 500);
setInterval(() => player.report(), 1000);
setInterval(() => view.burnInShift(), 120000);
