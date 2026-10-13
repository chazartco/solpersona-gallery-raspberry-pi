/**
 * What the agent and the player say to each other over the local WebSocket,
 * and the SolPersona shapes they carry (SPEC §4.2, §6.1, §8).
 *
 * The agent holds the credential and talks to SolPersona; the player is told
 * what to draw. Nothing here ever carries the access token.
 */

export interface ScreenItem {
  id: string;
  kind: 'IMAGE' | 'VIDEO';
  title: string;
  artist: { name: string; handle: string | null } | null;
  year: number | null;
  description: string | null;
  media: {
    original: string | null;
    display: string | null;
    poster: string | null;
    fallbacks: string[];
  };
  links?: { solpersona: string | null; vault: string | null; marketplace: string | null };
}

export interface TimelineSlot {
  id: string;
  ms: number;
  estimated?: boolean;
}

export type Corner = 'BOTTOM_LEFT' | 'BOTTOM_RIGHT' | 'TOP_LEFT' | 'TOP_RIGHT';

/** Resolved by the agent: every key present, out-of-range values clamped. */
export interface Settings {
  slideSeconds: number;
  order: 'GALLERY' | 'SHUFFLE';
  transition: 'FADE' | 'DISSOLVE' | 'CUT';
  transitionMs: number;
  loopShortVideos: boolean;
  maxVideoLoops: number;
  videoSound: boolean;
  fit: 'BLUR' | 'FRAME' | 'BLACK' | 'FILL';
  washOpacity: number;
  pieceShadow: boolean;
  frameStyle: 'GALLERY_WHITE' | 'MUSEUM_BLACK' | 'OAK' | 'WALNUT' | 'NONE';
  marginPercent: number;
  imageQuality: 'AUTO' | 'ORIGINAL' | 'DISPLAY';
  captionStyle: 'LABEL' | 'MINIMAL' | 'NONE';
  captionTiming: 'ALWAYS' | 'START';
  captionCorner: Corner;
  showTitle: boolean;
  showArtist: boolean;
  showYear: boolean;
  showDescription: boolean;
  qrTarget: 'NONE' | 'SOLPERSONA' | 'VAULT' | 'MARKETPLACE';
  qrCorner: Corner;
  burnInGuard: boolean;
  prefetch: number;
  schedule: { on: string; off: string } | null;
}

export interface ClockState {
  epochMs: number;
  pausedAtMs: number | null;
}

export interface Show extends ClockState {
  id: string;
  version: number;
  sourceRef?: string;
  settings: Settings;
  items: ScreenItem[];
  timeline: TimelineSlot[];
}

export interface Takeover {
  item: ScreenItem;
  settings: Settings;
  startedAt: string;
  until: string | null;
}

export interface Capabilities {
  screenWidth: number;
  screenHeight: number;
  storageMb: number;
  video: boolean;
  originals: boolean;
  persistentStorage: boolean;
}

export interface NetworkInfo {
  online: boolean | null;
  kind: 'ethernet' | 'wifi' | null;
  ssid: string | null;
  ip: string | null;
  hotspot: { ssid: string; secured: boolean; qr: string } | null;
  error: string | null;
}

export interface Registration {
  deviceId: string;
  pairingCode: string;
  pairUrl: string;
  qrDataUri: string | null;
  expiresAt: number | null;
}

export type Mode = 'boot' | 'pairing' | 'sample' | 'player';
export type Status = 'IDLE' | 'PLAYING' | 'UNLICENSED' | null;

export interface AgentState {
  type: 'state';
  mode: Mode;
  status: Status;
  device: { id: string; name: string; owner: string | null; keepOffline?: boolean } | null;
  registration: Registration | null;
  pairError: string | null;
  showSig: string | null;
  takeover: Takeover | null;
  /** The server's time when the agent sent this (SPEC §5.1, measured by the agent). */
  serverNow: number;
  clockSynced: boolean;
  online: boolean;
  lastError: string | null;
  capabilities: Capabilities;
  kept: { on: boolean; stored: number; total: number; error: string | null };
  dark: boolean;
  network: NetworkInfo | null;
  appVersion: string;
  debugAllowed: boolean;
  baseHost: string;
  baseUrl: string;
  debug?: AgentDebug;
}

export interface AgentDebug {
  offset: number | null;
  rtt: number | null;
  lastSyncAgoMs: number | null;
  nextSyncInMs: number | null;
  failures: number;
  pollMs: number;
  ackSeq: number;
  commands: { seq: number; type: string; at: number }[];
  pendingDurations: Record<string, number>;
  plays: number;
  deviceId: string | null;
  calls: { at: number; method: string; path: string; status: number | string; ms: number; note: string }[];
  store: { files: number; mb: number; capMb: number; running: string[]; states: Record<string, number>; roomFull: boolean };
  panel: { connector: string; width: number; height: number } | null;
  power: string;
}

export type AgentMessage =
  | AgentState
  | { type: 'show'; show: Show | null }
  | { type: 'stored'; urls: string[] }
  | { type: 'event'; event: 'identify' | 'reload' | 'clear-cache'; name?: string; ms?: number }
  | { type: 'key'; key: string };

/** What the player tells the agent about the wall, about once a second. */
export interface PlayerReport {
  itemId: string | null;
  index: number | null;
  paused: boolean;
  cached: number;
  bufferedAhead: number;
  error: string | null;
  visible: boolean;
}

export type PlayerMessage =
  | { type: 'report'; state: PlayerReport; inUse: string[] }
  | { type: 'duration'; itemId: string; ms: number }
  | { type: 'action'; action: 'sample' | 'stop-sample' | 'forget' | 'reload' }
  | { type: 'debug'; on: boolean };
