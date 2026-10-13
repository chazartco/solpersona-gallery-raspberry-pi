import { z } from 'zod';

/**
 * How a gallery plays on a screen (`0078`).
 *
 * One catalogue, like `DESIGN_AXES` for the profile: every setting a person is
 * offered, with its default, its label and the sentence the dashboard prints.
 * The Gallery Exhibition page builds its form by mapping `SCREEN_SETTING_GROUPS`,
 * the API validates with `screenSettingsPatch`, and a screen receives the
 * resolved object. Adding a setting is a field here plus the player reading it
 * — nothing is migrated, because `screen_settings.settings` is JSON validated in
 * code, and an unknown or out-of-range value falls back to its default per key
 * so an old row never breaks a screen.
 *
 * Resolution is three layers: these defaults, then the account's own defaults
 * (`source_ref = 'default'`), then the gallery's overrides. A gallery stores
 * only what it changed, so changing an account default reaches every gallery
 * that did not say otherwise.
 */

export const FIT_MODES = ['BLUR', 'FRAME', 'BLACK', 'FILL'] as const;
export const FRAME_STYLES = ['GALLERY_WHITE', 'MUSEUM_BLACK', 'OAK', 'WALNUT', 'NONE'] as const;
export const TRANSITIONS = ['FADE', 'DISSOLVE', 'CUT'] as const;
export const ORDERS = ['GALLERY', 'SHUFFLE'] as const;
export const CAPTION_STYLES = ['LABEL', 'MINIMAL', 'NONE'] as const;
export const CAPTION_TIMINGS = ['ALWAYS', 'START'] as const;
export const CORNERS = ['BOTTOM_LEFT', 'BOTTOM_RIGHT', 'TOP_LEFT', 'TOP_RIGHT'] as const;
export const QR_TARGETS = ['NONE', 'SOLPERSONA', 'VAULT', 'MARKETPLACE'] as const;
export const IMAGE_QUALITIES = ['AUTO', 'ORIGINAL', 'DISPLAY'] as const;

const time = z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/, 'Use HH:MM, 24-hour.');

/** Every setting, with its default. `.catch` makes a bad stored value fall back rather than fail. */
export const screenSettingsSchema = z.object({
  /** Seconds each still stays up. */
  slideSeconds: z.number().int().min(5).max(3600).catch(30),
  /** Pieces in gallery order or shuffled (a new shuffle each time the show is built). */
  order: z.enum(ORDERS).catch('GALLERY'),
  transition: z.enum(TRANSITIONS).catch('FADE'),
  /** How long the change between pieces takes. Ignored for CUT. */
  transitionMs: z.number().int().min(0).max(5000).catch(1200),

  /**
   * A video always plays to its end — a film cut off halfway through is worse
   * than a slideshow that runs long. These decide what happens around that.
   */
  /** A video shorter than the slide time plays again until the time is up (whole plays only). */
  loopShortVideos: z.boolean().catch(true),
  /** Plays of one video at most, even if that leaves it shorter than the slide time. */
  maxVideoLoops: z.number().int().min(1).max(20).catch(3),
  /** Sound. Off by default: a room full of work is not a room full of soundtracks. */
  videoSound: z.boolean().catch(false),

  /** What fills the screen around a piece that is not the screen's shape. */
  fit: z.enum(FIT_MODES).catch('BLUR'),
  /** For BLUR: how strongly the blurred wash shows, 0–100. Lower is more black, quieter. */
  washOpacity: z.number().int().min(0).max(100).catch(60),
  /** For BLUR: a soft shadow under the piece, lifting it off the wash. */
  pieceShadow: z.boolean().catch(true),
  /** For FRAME: the moulding and mat drawn around the piece. */
  frameStyle: z.enum(FRAME_STYLES).catch('GALLERY_WHITE'),
  /** Space kept clear around the piece, as a percentage of the screen's shorter side. */
  marginPercent: z.number().int().min(0).max(20).catch(4),
  /** Which file a still is shown from: the original, the 1000px display copy, or the screen decides. */
  imageQuality: z.enum(IMAGE_QUALITIES).catch('AUTO'),

  /** The wall label. */
  captionStyle: z.enum(CAPTION_STYLES).catch('LABEL'),
  captionTiming: z.enum(CAPTION_TIMINGS).catch('ALWAYS'),
  captionCorner: z.enum(CORNERS).catch('BOTTOM_LEFT'),
  showTitle: z.boolean().catch(true),
  showArtist: z.boolean().catch(true),
  showYear: z.boolean().catch(true),
  showDescription: z.boolean().catch(false),

  /** A code a visitor scans to take the piece away with them. */
  qrTarget: z.enum(QR_TARGETS).catch('SOLPERSONA'),
  qrCorner: z.enum(CORNERS).catch('BOTTOM_RIGHT'),

  /** Moves the label and code a few pixels now and then, so an OLED does not remember them. */
  burnInGuard: z.boolean().catch(true),
  /** Pieces fetched ahead of the one on screen. The screen may hold fewer if it is short of room. */
  prefetch: z.number().int().min(1).max(10).catch(2),

  /** Hours the screen shows work, in the screen's own time zone; outside them it goes dark. Null: always on. */
  schedule: z
    .object({ on: time, off: time })
    .nullable()
    .catch(null),
});

export type ScreenSettings = z.infer<typeof screenSettingsSchema>;

/** The platform's defaults. */
export const DEFAULT_SCREEN_SETTINGS: ScreenSettings = screenSettingsSchema.parse({});

/**
 * What a person may send: any subset, strictly typed (a bad value is a 422
 * rather than a silent fallback — the person should be told), and `null` on a
 * key to clear an override so the layer underneath shows through.
 */
export const screenSettingsPatch = z
  .object({
    slideSeconds: z.number().int().min(5).max(3600),
    order: z.enum(ORDERS),
    transition: z.enum(TRANSITIONS),
    transitionMs: z.number().int().min(0).max(5000),
    loopShortVideos: z.boolean(),
    maxVideoLoops: z.number().int().min(1).max(20),
    videoSound: z.boolean(),
    fit: z.enum(FIT_MODES),
    washOpacity: z.number().int().min(0).max(100),
    pieceShadow: z.boolean(),
    frameStyle: z.enum(FRAME_STYLES),
    marginPercent: z.number().int().min(0).max(20),
    imageQuality: z.enum(IMAGE_QUALITIES),
    captionStyle: z.enum(CAPTION_STYLES),
    captionTiming: z.enum(CAPTION_TIMINGS),
    captionCorner: z.enum(CORNERS),
    showTitle: z.boolean(),
    showArtist: z.boolean(),
    showYear: z.boolean(),
    showDescription: z.boolean(),
    qrTarget: z.enum(QR_TARGETS),
    qrCorner: z.enum(CORNERS),
    burnInGuard: z.boolean(),
    prefetch: z.number().int().min(1).max(10),
    schedule: z.object({ on: time, off: time }).nullable(),
  })
  .partial()
  .strict();

export type ScreenSettingsPatch = z.infer<typeof screenSettingsPatch>;

/**
 * Only the keys a layer actually set, dropping anything unknown or invalid —
 * so a stored layer can never carry a value the schema would refuse.
 */
export function cleanLayer(raw: unknown): Partial<ScreenSettings> {
  if (!raw || typeof raw !== 'object') return {};
  const out: Record<string, unknown> = {};
  const shape = screenSettingsPatch.shape as Record<string, z.ZodTypeAny>;
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    const field = shape[key];
    if (!field) continue;
    const parsed = field.safeParse(value);
    if (parsed.success && parsed.data !== undefined) out[key] = parsed.data;
  }
  return out as Partial<ScreenSettings>;
}

/** Applies a patch to a stored layer: a value sets the key, `null` clears it (except `schedule`, where null means "always on"). */
export function applyPatch(layer: Partial<ScreenSettings>, patch: Record<string, unknown>): Partial<ScreenSettings> {
  const next: Record<string, unknown> = { ...layer };
  for (const [key, value] of Object.entries(patch)) {
    if (value === undefined) continue;
    next[key] = value;
  }
  return cleanLayer(next);
}

/** Defaults, then the account's, then the gallery's. */
export function resolveScreenSettings(...layers: unknown[]): ScreenSettings {
  return screenSettingsSchema.parse(Object.assign({}, ...layers.map(cleanLayer)));
}

// ---------------------------------------------------------------------------
// The catalogue the dashboard draws.
// ---------------------------------------------------------------------------

export type SettingControl =
  | { type: 'choice'; options: readonly { value: string; label: string }[] }
  | { type: 'switch' }
  | { type: 'number'; min: number; max: number; step?: number; unit: string }
  | { type: 'schedule' };

export interface SettingField {
  key: keyof ScreenSettings;
  label: string;
  help: string;
  control: SettingControl;
  /** Shown only when this says so — e.g. the frame style only under FRAME. */
  when?: (settings: ScreenSettings) => boolean;
}

export interface SettingGroup {
  id: string;
  title: string;
  fields: SettingField[];
}

const cornerOptions = [
  { value: 'BOTTOM_LEFT', label: 'Bottom left' },
  { value: 'BOTTOM_RIGHT', label: 'Bottom right' },
  { value: 'TOP_LEFT', label: 'Top left' },
  { value: 'TOP_RIGHT', label: 'Top right' },
] as const;

export const SCREEN_SETTING_GROUPS: SettingGroup[] = [
  {
    id: 'timing',
    title: 'Timing',
    fields: [
      {
        key: 'slideSeconds',
        label: 'Time on each piece',
        help: 'How long a still stays up. A video always plays to its end.',
        control: { type: 'number', min: 5, max: 3600, step: 5, unit: 'seconds' },
      },
      {
        key: 'order',
        label: 'Order',
        help: 'Shuffled is a new order every time the show starts.',
        control: {
          type: 'choice',
          options: [
            { value: 'GALLERY', label: 'Gallery order' },
            { value: 'SHUFFLE', label: 'Shuffled' },
          ],
        },
      },
      {
        key: 'transition',
        label: 'Change between pieces',
        help: 'Dissolve is a slow cross-fade; Cut changes at once.',
        control: {
          type: 'choice',
          options: [
            { value: 'FADE', label: 'Fade through black' },
            { value: 'DISSOLVE', label: 'Dissolve' },
            { value: 'CUT', label: 'Cut' },
          ],
        },
      },
      {
        key: 'transitionMs',
        label: 'Change length',
        help: 'How long the fade or dissolve takes.',
        control: { type: 'number', min: 0, max: 5000, step: 100, unit: 'ms' },
        when: (s) => s.transition !== 'CUT',
      },
      {
        key: 'schedule',
        label: 'Opening hours',
        help: 'Outside these hours the screen goes dark, in its own time zone. Off: always on.',
        control: { type: 'schedule' },
      },
    ],
  },
  {
    id: 'video',
    title: 'Video',
    fields: [
      {
        key: 'loopShortVideos',
        label: 'Repeat short videos',
        help: 'A video shorter than the time on each piece plays again until the time is up — always whole plays, never cut off.',
        control: { type: 'switch' },
      },
      {
        key: 'maxVideoLoops',
        label: 'Plays at most',
        help: 'However short a video is, it plays no more than this many times in a row.',
        control: { type: 'number', min: 1, max: 20, unit: 'plays' },
        when: (s) => s.loopShortVideos,
      },
      {
        key: 'videoSound',
        label: 'Play sound',
        help: 'Off by default — most rooms want quiet.',
        control: { type: 'switch' },
      },
    ],
  },
  {
    id: 'presentation',
    title: 'Presentation',
    fields: [
      {
        key: 'fit',
        label: 'Around the piece',
        help: 'What fills the screen when a piece is not the screen’s shape. Nothing is ever cropped except under Fill.',
        control: {
          type: 'choice',
          options: [
            { value: 'BLUR', label: 'Blurred wash of the piece' },
            { value: 'FRAME', label: 'Frame and mat' },
            { value: 'BLACK', label: 'Black' },
            { value: 'FILL', label: 'Fill the screen (crops)' },
          ],
        },
      },
      {
        key: 'washOpacity',
        label: 'Strength of the wash',
        help: 'How strongly the blurred colour of the piece shows around it. Lower is darker and quieter; higher is fuller colour.',
        control: { type: 'number', min: 0, max: 100, step: 5, unit: '%' },
        when: (s) => s.fit === 'BLUR',
      },
      {
        key: 'pieceShadow',
        label: 'Shadow under the piece',
        help: 'A soft shadow that lifts the piece off the wash, as if it hung in front of it.',
        control: { type: 'switch' },
        when: (s) => s.fit === 'BLUR',
      },
      {
        key: 'frameStyle',
        label: 'Frame',
        help: 'The moulding and mat drawn around the piece.',
        control: {
          type: 'choice',
          options: [
            { value: 'GALLERY_WHITE', label: 'Gallery white' },
            { value: 'MUSEUM_BLACK', label: 'Museum black' },
            { value: 'OAK', label: 'Oak' },
            { value: 'WALNUT', label: 'Walnut' },
            { value: 'NONE', label: 'Mat only' },
          ],
        },
        when: (s) => s.fit === 'FRAME',
      },
      {
        key: 'marginPercent',
        label: 'Breathing room',
        help: 'Space kept clear around the piece.',
        control: { type: 'number', min: 0, max: 20, unit: '%' },
        when: (s) => s.fit !== 'FILL',
      },
      {
        key: 'imageQuality',
        label: 'Image quality',
        help: 'Original is the artist’s full file; Display is a 1000px copy for slow connections. Auto lets the screen choose by its size and storage.',
        control: {
          type: 'choice',
          options: [
            { value: 'AUTO', label: 'Auto' },
            { value: 'ORIGINAL', label: 'Original' },
            { value: 'DISPLAY', label: 'Display copy' },
          ],
        },
      },
    ],
  },
  {
    id: 'label',
    title: 'Wall label',
    fields: [
      {
        key: 'captionStyle',
        label: 'Label',
        help: 'A museum card, a single quiet line, or nothing.',
        control: {
          type: 'choice',
          options: [
            { value: 'LABEL', label: 'Museum card' },
            { value: 'MINIMAL', label: 'One line' },
            { value: 'NONE', label: 'None' },
          ],
        },
      },
      {
        key: 'captionTiming',
        label: 'When',
        help: 'Always, or for the first few seconds of each piece.',
        control: {
          type: 'choice',
          options: [
            { value: 'ALWAYS', label: 'The whole time' },
            { value: 'START', label: 'At the start of each piece' },
          ],
        },
        when: (s) => s.captionStyle !== 'NONE',
      },
      {
        key: 'captionCorner',
        label: 'Where',
        help: 'The corner the label sits in.',
        control: { type: 'choice', options: cornerOptions },
        when: (s) => s.captionStyle !== 'NONE',
      },
      { key: 'showTitle', label: 'Title', help: 'The piece’s title.', control: { type: 'switch' }, when: (s) => s.captionStyle !== 'NONE' },
      { key: 'showArtist', label: 'Artist', help: 'Who made it.', control: { type: 'switch' }, when: (s) => s.captionStyle !== 'NONE' },
      { key: 'showYear', label: 'Year', help: 'When it was minted or made.', control: { type: 'switch' }, when: (s) => s.captionStyle !== 'NONE' },
      {
        key: 'showDescription',
        label: 'Description',
        help: 'The first lines of the piece’s description, on the museum card only.',
        control: { type: 'switch' },
        when: (s) => s.captionStyle === 'LABEL',
      },
    ],
  },
  {
    id: 'qr',
    title: 'QR code',
    fields: [
      {
        key: 'qrTarget',
        label: 'Code leads to',
        help: 'What a visitor reaches by scanning. Where a piece has no such page, the next one along is used.',
        control: {
          type: 'choice',
          options: [
            { value: 'SOLPERSONA', label: 'The piece on SolPersona' },
            { value: 'VAULT', label: 'The gallery on TheVault' },
            { value: 'MARKETPLACE', label: 'The marketplace' },
            { value: 'NONE', label: 'No code' },
          ],
        },
      },
      {
        key: 'qrCorner',
        label: 'Where',
        help: 'The corner the code sits in.',
        control: { type: 'choice', options: cornerOptions },
        when: (s) => s.qrTarget !== 'NONE',
      },
    ],
  },
  {
    id: 'screen',
    title: 'Screen care',
    fields: [
      {
        key: 'burnInGuard',
        label: 'Burn-in guard',
        help: 'Moves the label and code a few pixels now and then, so an OLED screen does not keep a ghost of them.',
        control: { type: 'switch' },
      },
      {
        key: 'prefetch',
        label: 'Load ahead',
        help: 'Pieces downloaded before they are due. The screen may hold fewer if it is short of room.',
        control: { type: 'number', min: 1, max: 10, unit: 'pieces' },
      },
    ],
  },
];
