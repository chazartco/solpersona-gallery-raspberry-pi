import type { AgentMessage, PlayerMessage } from './protocol';

/**
 * The line to the local agent. The page normally comes from the agent itself
 * (http://127.0.0.1:8737/); under `vite dev`, or with `?agent=…`, it talks to
 * one elsewhere. If the agent restarts, the page keeps playing what it has by
 * the last clock and reconnects.
 */
export class AgentLink {
  readonly base: string;
  connected = false;
  private ws: WebSocket | null = null;
  private retryMs = 500;
  private listeners: ((m: AgentMessage) => void)[] = [];
  private onConnect: (() => void)[] = [];

  constructor() {
    const fromQuery = new URLSearchParams(location.search).get('agent');
    this.base = (fromQuery || (import.meta.env.DEV || location.protocol === 'file:' ? 'http://127.0.0.1:8737' : location.origin)).replace(/\/+$/, '');
  }

  on(listener: (m: AgentMessage) => void): void {
    this.listeners.push(listener);
  }

  onOpen(listener: () => void): void {
    this.onConnect.push(listener);
  }

  connect(): void {
    const ws = new WebSocket(this.base.replace(/^http/, 'ws') + '/ws');
    this.ws = ws;
    ws.onopen = () => {
      this.connected = true;
      this.retryMs = 500;
      this.onConnect.forEach((f) => f());
    };
    ws.onmessage = (event) => {
      let message: AgentMessage;
      try {
        message = JSON.parse(String(event.data)) as AgentMessage;
      } catch {
        return;
      }
      this.listeners.forEach((f) => f(message));
    };
    ws.onclose = () => {
      this.connected = false;
      if (this.ws !== ws) return;
      setTimeout(() => this.connect(), this.retryMs);
      this.retryMs = Math.min(5000, this.retryMs * 2);
    };
    ws.onerror = () => ws.close();
  }

  send(message: PlayerMessage): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(message));
  }

  /** A media file through the agent: from the card when stored, else redirected to its host. */
  mediaUrl(url: string): string {
    return `${this.base}/media?u=${encodeURIComponent(url)}`;
  }

  qrUrl(itemId: string, sample: boolean): string {
    return `${this.base}/qr?item=${encodeURIComponent(itemId)}${sample ? '&sample=1' : ''}`;
  }
}
