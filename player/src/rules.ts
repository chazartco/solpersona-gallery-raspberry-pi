import type { ScreenItem, Settings } from './protocol';

/** Opening hours (SPEC §8): device local time; `on === off` is always on; `off < on` spans midnight. */
export function scheduleOpen(schedule: Settings['schedule'], minutes: number): boolean {
  if (!schedule || !schedule.on || !schedule.off) return true;
  const on = toMinutes(schedule.on);
  const off = toMinutes(schedule.off);
  if (on === null || off === null || on === off) return true;
  return on < off ? minutes >= on && minutes < off : minutes >= on || minutes < off;
}

export function toMinutes(text: string): number | null {
  const m = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(text);
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}

export function minutesOf(date: Date): number {
  return date.getHours() * 60 + date.getMinutes();
}

/**
 * Which files would do for a piece, best first (SPEC §6.2): the chosen copy,
 * the other, the fallbacks, then the poster. A film is its film.
 */
export function chooseCandidates(
  item: Pick<ScreenItem, 'kind' | 'media'>,
  quality: Settings['imageQuality'],
  screenWidth: number,
  canHoldOriginal: boolean,
): string[] {
  const m = item.media ?? ({} as ScreenItem['media']);
  const fallbacks = Array.isArray(m.fallbacks) ? m.fallbacks : [];
  let list: (string | null | undefined)[];
  if (item.kind === 'VIDEO') {
    list = [m.original, ...fallbacks];
  } else {
    const preferOriginal = quality === 'ORIGINAL' || (quality !== 'DISPLAY' && screenWidth > 1280 && canHoldOriginal);
    list = [...(preferOriginal ? [m.original, m.display] : [m.display, m.original]), ...fallbacks, m.poster];
  }
  const seen = new Set<string>();
  return list.filter((url): url is string => {
    if (!url || seen.has(url)) return false;
    seen.add(url);
    return true;
  });
}

/** The file a still is shown from when everything loads (test-vectors `imageChoice`). */
export function imageChoice(
  quality: Settings['imageQuality'],
  screenWidth: number,
  canHoldOriginal: boolean,
  original: string | null,
  display: string | null,
): string | null {
  const urls = chooseCandidates({ kind: 'IMAGE', media: { original, display, poster: null, fallbacks: [] } }, quality, screenWidth, canHoldOriginal);
  return urls[0] ?? null;
}

/** The keys that change how a piece is drawn: a change redraws the wall at once (SPEC §8). */
export const PRESENTATION_KEYS: (keyof Settings)[] = [
  'fit', 'washOpacity', 'pieceShadow', 'frameStyle', 'marginPercent', 'imageQuality', 'captionStyle', 'captionTiming',
  'captionCorner', 'showTitle', 'showArtist', 'showYear', 'showDescription', 'qrTarget', 'qrCorner', 'videoSound',
];

export function settingsSig(s: Settings): string {
  return hash(PRESENTATION_KEYS.map((k) => `${k}=${String(s[k])}`).join('&'));
}

/** Small stable hash for cache keys. */
export function hash(str: string): string {
  let h = 5381;
  for (let i = 0; i < str.length; i += 1) h = ((h << 5) + h + str.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}

/** The wall label's text, per the settings; null when it would be empty. */
export function labelParts(item: ScreenItem, s: Settings) {
  if (s.captionStyle === 'NONE') return null;
  const title = s.showTitle && item.title ? item.title : null;
  const artist = s.showArtist && item.artist && item.artist.name ? item.artist.name : null;
  const year = s.showYear && item.year ? String(item.year) : null;
  const description = s.captionStyle === 'LABEL' && s.showDescription && item.description ? item.description : null;
  if (!title && !artist && !year && !description) return null;
  return { title, artist, year, description };
}
