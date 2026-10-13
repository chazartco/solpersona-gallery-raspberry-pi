#!/usr/bin/env node
// Look at the wall from a script (development aid): opens the player in a
// headless Chrome/Edge, waits in real time, runs expressions in the page,
// presses keys and takes screenshots — over the DevTools protocol, no packages.
//
//   node dev/cdp.mjs --url http://localhost:8737/ --size 1920x1080 \
//     --wait 6000 --shot out/playing.png --eval "document.title" --key Enter --wait 2000 --shot out/overlay.png
//
// Steps run in the order given. Needs Node 22+ (global WebSocket) and Chrome or Edge.
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const args = process.argv.slice(2);
const opt = (name, fallback) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : fallback;
};
const url = opt('--url', 'http://localhost:8737/');
const [width, height] = opt('--size', '1920x1080').split('x').map(Number);
const port = 9333 + Math.floor(Math.random() * 500);
const browsers = [
  process.env.BROWSER,
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  '/usr/bin/chromium', '/usr/bin/google-chrome',
].filter(Boolean);
const exe = browsers.find((b) => existsSync(b));
if (!exe) throw new Error('no Chrome or Edge found; set BROWSER');

const profile = mkdtempSync(join(tmpdir(), 'spg-cdp-'));
const child = spawn(exe, [
  '--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, `--window-size=${width},${height}`,
  '--hide-scrollbars', '--autoplay-policy=no-user-gesture-required', '--no-first-run', 'about:blank',
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let ws;
for (let i = 0; i < 50 && !ws; i += 1) {
  await sleep(200);
  try {
    const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
    const page = targets.find((t) => t.type === 'page');
    if (page) ws = new WebSocket(page.webSocketDebuggerUrl);
  } catch { /* not up yet */ }
}
await new Promise((r) => ws.addEventListener('open', r, { once: true }));
let id = 0;
const pending = new Map();
ws.addEventListener('message', (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) {
    pending.get(m.id)(m);
    pending.delete(m.id);
  }
});
const send = (method, params = {}) => new Promise((resolve) => {
  id += 1;
  pending.set(id, resolve);
  ws.send(JSON.stringify({ id, method, params }));
});

await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url });

for (let i = 0; i < args.length; i += 1) {
  const a = args[i];
  const v = args[i + 1];
  if (a === '--wait') { await sleep(Number(v)); i += 1; }
  else if (a === '--shot') {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(v, Buffer.from(r.result.data, 'base64'));
    console.log('shot', v);
    i += 1;
  } else if (a === '--eval') {
    const r = await send('Runtime.evaluate', { expression: v, returnByValue: true, awaitPromise: true });
    console.log('eval', JSON.stringify(r.result.result.value ?? r.result.exceptionDetails?.text ?? r.result.result.description));
    i += 1;
  } else if (a === '--key') {
    for (const type of ['keyDown', 'keyUp']) await send('Input.dispatchKeyEvent', { type, key: v, code: v, windowsVirtualKeyCode: v === 'Enter' ? 13 : v === 'Escape' ? 27 : 0 });
    i += 1;
  } else if (a === '--url' || a === '--size') i += 1;
}
ws.close();
child.kill();
process.exit(0);
