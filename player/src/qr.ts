import qrcode from 'qrcode-generator';
import type { AgentLink } from './agent';

/**
 * Two kinds of QR code.
 *
 * A piece's code (SPEC §9) needs the screen's token, so the agent fetches it
 * and keeps it on the card; the page asks the agent and holds the PNG as an
 * object URL. Codes for the setup network, and the pairing code when the
 * server sent no picture, are drawn here — there may be no network at all.
 */

/** A crisp, scalable QR code for `text`, as a data URL. */
export function qrDataUrl(text: string): string {
  const qr = qrcode(0, 'M');
  qr.addData(text);
  qr.make();
  const svg = qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
  return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
}

interface QrEntry {
  state: 'loading' | 'ready' | 'none' | 'failed';
  url: string | null;
  at: number;
}

export class QrCache {
  private map = new Map<string, QrEntry>();
  onReady: () => void = () => {};

  constructor(private agent: AgentLink) {}

  static key(itemId: string, target: string, takeover: boolean): string {
    return itemId + '|' + target + (takeover ? '|T' : '');
  }

  request(itemId: string, target: string, takeover: boolean, sample: boolean): void {
    if (target === 'NONE') return;
    const key = QrCache.key(itemId, target, takeover);
    const have = this.map.get(key);
    if (have && (have.state !== 'failed' || Date.now() - have.at < 30000)) return;
    const entry: QrEntry = { state: 'loading', url: null, at: Date.now() };
    this.map.set(key, entry);
    fetch(this.agent.qrUrl(itemId, sample), { cache: 'no-store' })
      .then(async (res) => {
        if (this.map.get(key) !== entry) return; // cleared meanwhile
        if (res.ok) {
          entry.url = URL.createObjectURL(await res.blob());
          entry.state = 'ready';
          this.onReady();
        } else if (res.status === 404) {
          entry.state = 'none'; // the piece has no link: no code, quietly
        } else {
          entry.state = 'failed';
          entry.at = Date.now();
        }
      })
      .catch(() => {
        entry.state = 'failed';
        entry.at = Date.now();
      });
  }

  urlFor(key: string): string | null {
    const entry = this.map.get(key);
    return entry && entry.state === 'ready' ? entry.url : null;
  }

  states(): [string, string][] {
    return [...this.map.entries()].map(([k, v]) => [k, v.state]);
  }

  prune(keep: Set<string>): void {
    this.map.forEach((entry, key) => {
      if (keep.has(key)) return;
      if (entry.url) URL.revokeObjectURL(entry.url);
      this.map.delete(key);
    });
  }

  clear(): void {
    this.prune(new Set());
  }
}
