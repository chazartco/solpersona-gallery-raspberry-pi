import { describe, expect, it } from 'vitest';
import type { NetworkInfo } from '../src/protocol';
import { downloadLine, fmtBytes, networkLine } from '../src/view';

const base: NetworkInfo = { online: true, kind: null, ssid: null, ip: null, hotspot: null, error: null };

describe('what the screen says about its network and downloads', () => {
  it('names the connection traffic goes over, and the other one', () => {
    expect(networkLine({
      ...base, kind: 'ethernet', ip: '192.168.1.41', via: 'eth0',
      devices: [{ device: 'eth0', type: 'ethernet', ip: '192.168.1.41', connection: 'Wired' }, { device: 'wlan0', type: 'wifi', ip: '192.168.1.40', connection: 'Home' }],
    })).toBe('Ethernet (eth0) · 192.168.1.41 · also Wi-Fi');
    expect(networkLine({
      ...base, kind: 'wifi', ssid: 'Home', ip: '192.168.1.40', via: 'wlan0',
      wifi: { ssid: 'Home', signal: 72, freqMhz: 5180, band: '5 GHz' },
    })).toBe('Wi-Fi Home (wlan0) · 72% · 5 GHz · 192.168.1.40');
    expect(networkLine({ ...base, online: false })).toBe('not connected');
  });

  it('shows progress and speed', () => {
    expect(downloadLine({ itemId: 'a:1', file: 'cdn.example/media/film.mp4', bytes: 157286400, total: 262144000, speed: 838861, seconds: 300 }))
      .toBe('film.mp4 60% of 250.0 MB · 819 KB/s');
    expect(fmtBytes(null)).toBe('?');
  });
});
