"""The rules every SolPersona player shares, ported from the reference.

`position_at` mirrors `reference/screen-timeline.ts` line for line, and the
rest mirror the reference simulator. The player (TypeScript) carries the same
rules for drawing; the agent needs them to decide which file to download next,
whether the panel should be on, and what to report. Both ports are checked
against `docs/solpersona/test-vectors.json` in CI.
"""

from __future__ import annotations

import math
import re
import time
from typing import Any, Iterable, Optional

# ---------------------------------------------------------------------------
# The clock (SPEC §5.2)
# ---------------------------------------------------------------------------


def timeline_length(timeline: list[dict]) -> float:
    return sum(slot["ms"] for slot in timeline)


def start_of(timeline: list[dict], index: int) -> float:
    at = 0
    for i in range(min(index, len(timeline))):
        at += timeline[i]["ms"]
    return at


def position_at(timeline: list[dict], clock: dict, now_ms: float) -> Optional[dict]:
    """Where the show is at `now_ms`: {index, offsetMs, lap}, or None for an empty show."""
    total = timeline_length(timeline)
    if not timeline or total <= 0:
        return None
    paused = clock.get("pausedAtMs")
    at = (paused if paused is not None else now_ms) - clock["epochMs"]
    lap = math.floor(at / total)
    into = at - lap * total
    for index, slot in enumerate(timeline):
        ms = slot["ms"]
        if into < ms:
            return {"index": index, "offsetMs": into, "lap": lap}
        into -= ms
    return {"index": len(timeline) - 1, "offsetMs": timeline[-1]["ms"], "lap": lap}


# ---------------------------------------------------------------------------
# Opening hours (SPEC §8 `schedule`)
# ---------------------------------------------------------------------------

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def to_minutes(text: Any) -> Optional[int]:
    if not isinstance(text, str):
        return None
    m = _HHMM.match(text)
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def schedule_open(schedule: Optional[dict], minutes: int) -> bool:
    """Is the screen within its opening hours at `minutes` past local midnight?

    `on == off` means always on; `off < on` spans midnight.
    """
    if not schedule:
        return True
    on = to_minutes(schedule.get("on"))
    off = to_minutes(schedule.get("off"))
    if on is None or off is None or on == off:
        return True
    if on < off:
        return on <= minutes < off
    return minutes >= on or minutes < off


def local_minutes(server_ms: float) -> int:
    """Minutes past midnight in the device's own time zone, at server time.

    Server time, not the Pi's wall clock: the wall clock may not have been set
    yet, but the offset from the last sync has.
    """
    t = time.localtime(server_ms / 1000.0)
    return t.tm_hour * 60 + t.tm_min


# ---------------------------------------------------------------------------
# Which file to show (SPEC §6.2)
# ---------------------------------------------------------------------------


def _dedupe(urls: Iterable[Optional[str]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if not url or not isinstance(url, str) or url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def prefers_original(quality: str, screen_width: int, can_hold_original: bool) -> bool:
    return quality == "ORIGINAL" or (quality != "DISPLAY" and screen_width > 1280 and can_hold_original)


def choose_candidates(item: dict, settings: dict, screen_width: int, can_hold_original: bool) -> list[str]:
    """Every file that would do for `item`, best first: the chosen copy, the
    other copy, the fallbacks, then the poster. A video is its film."""
    media = item.get("media") or {}
    fallbacks = media.get("fallbacks") if isinstance(media.get("fallbacks"), list) else []
    if item.get("kind") == "VIDEO":
        return _dedupe([media.get("original"), *fallbacks])
    original, display = media.get("original"), media.get("display")
    first = [original, display] if prefers_original(settings.get("imageQuality", "AUTO"), screen_width, can_hold_original) else [display, original]
    return _dedupe([*first, *fallbacks, media.get("poster")])


def image_choice(quality: str, screen_width: int, can_hold_original: bool, original: Optional[str], display: Optional[str]) -> Optional[str]:
    """The file a still is shown from, when everything loads (test-vectors `imageChoice`)."""
    item = {"kind": "IMAGE", "media": {"original": original, "display": display, "poster": None, "fallbacks": []}}
    urls = choose_candidates(item, {"imageQuality": quality}, screen_width, can_hold_original)
    return urls[0] if urls else None


def video_still(item: dict) -> Optional[str]:
    """A film's still: the BLUR wash and the poster under it."""
    media = item.get("media") or {}
    return media.get("display") or media.get("poster")


# ---------------------------------------------------------------------------
# Presentation settings (SPEC §8): every key present, out-of-range clamped
# ---------------------------------------------------------------------------

CORNERS = ("BOTTOM_LEFT", "BOTTOM_RIGHT", "TOP_LEFT", "TOP_RIGHT")

DEFAULT_SETTINGS: dict[str, Any] = {
    "slideSeconds": 30,
    "order": "GALLERY",
    "transition": "FADE",
    "transitionMs": 1200,
    "loopShortVideos": True,
    "maxVideoLoops": 3,
    "videoSound": False,
    "fit": "BLUR",
    "washOpacity": 60,
    "pieceShadow": True,
    "frameStyle": "GALLERY_WHITE",
    "marginPercent": 4,
    "imageQuality": "AUTO",
    "captionStyle": "LABEL",
    "captionTiming": "ALWAYS",
    "captionCorner": "BOTTOM_LEFT",
    "showTitle": True,
    "showArtist": True,
    "showYear": True,
    "showDescription": False,
    "qrTarget": "SOLPERSONA",
    "qrCorner": "BOTTOM_RIGHT",
    "burnInGuard": True,
    "prefetch": 2,
    "schedule": None,
}

_ENUMS: dict[str, tuple[str, ...]] = {
    "order": ("GALLERY", "SHUFFLE"),
    "transition": ("FADE", "DISSOLVE", "CUT"),
    "fit": ("BLUR", "FRAME", "BLACK", "FILL"),
    "frameStyle": ("GALLERY_WHITE", "MUSEUM_BLACK", "OAK", "WALNUT", "NONE"),
    "imageQuality": ("AUTO", "ORIGINAL", "DISPLAY"),
    "captionStyle": ("LABEL", "MINIMAL", "NONE"),
    "captionTiming": ("ALWAYS", "START"),
    "captionCorner": CORNERS,
    "qrTarget": ("NONE", "SOLPERSONA", "VAULT", "MARKETPLACE"),
    "qrCorner": CORNERS,
}

_INTS: dict[str, tuple[int, int]] = {
    "slideSeconds": (5, 3600),
    "transitionMs": (0, 5000),
    "maxVideoLoops": (1, 20),
    "washOpacity": (0, 100),
    "marginPercent": (0, 20),
    "prefetch": (1, 10),
}


def normalise_settings(raw: Any) -> dict[str, Any]:
    """The full settings object, whatever arrived: unknown keys dropped, bad
    values defaulted, numbers clamped into range (SPEC §8)."""
    src = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULT_SETTINGS)
    for key, default in DEFAULT_SETTINGS.items():
        if key not in src:
            continue
        value = src[key]
        if key in _ENUMS:
            out[key] = value if value in _ENUMS[key] else default
        elif key in _INTS:
            lo, hi = _INTS[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                out[key] = default
            else:
                out[key] = max(lo, min(hi, int(round(value))))
        elif key == "schedule":
            if isinstance(value, dict) and to_minutes(value.get("on")) is not None and to_minutes(value.get("off")) is not None:
                out[key] = {"on": value["on"], "off": value["off"]}
            else:
                out[key] = None
        else:  # booleans
            out[key] = value if isinstance(value, bool) else default
    return out
