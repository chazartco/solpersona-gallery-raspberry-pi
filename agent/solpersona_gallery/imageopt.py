"""Screen-sized copies of very large originals (SPEC §6.2: "decode at screen size
where the platform allows it").

An artist's original can be tens of megapixels. Chromium on a Pi decodes it at
full size every time it comes up — hundreds of megabytes, and a stall in the
transition. So when an original is much larger than it can ever be drawn, the
agent stores a copy just large enough to *cover* the panel (which is the
largest any `fit` draws it: FILL), at very high quality, with its colour
profile and orientation kept. Everything else — animated GIFs, SVG, AVIF,
anything Pillow cannot read, anything already near panel size — is stored
exactly as it came.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

# Only formats Pillow reads well and Chromium shows the same either way.
RESIZABLE = {"JPEG", "PNG", "WEBP", "TIFF", "BMP"}
# Below this, a copy saves too little to be worth a second encode.
MIN_SHRINK = 0.8


def cover_size(width: int, height: int, panel_w: int, panel_h: int) -> tuple[int, int, float]:
    scale = max(panel_w / width, panel_h / height)
    return max(1, round(width * scale)), max(1, round(height * scale)), scale


def make_optimiser(panel: Callable[[], Optional[tuple[int, int]]]):
    """An optimiser for MediaStore: (path, mime) → (path, mime, [w, h]) or None."""

    def optimise(path: Path, mime: str):
        size = panel()
        if not size:
            return None
        try:
            from PIL import Image, ImageOps
        except ImportError:
            return None
        Image.MAX_IMAGE_PIXELS = 400_000_000  # an original may be huge; a decompression bomb is not
        with Image.open(path) as im:
            if im.format not in RESIZABLE or getattr(im, "is_animated", False):
                return None
            width, height = im.size
            if im.getexif().get(0x0112, 1) in (5, 6, 7, 8):  # rotated a quarter turn: compare as shown
                width, height = height, width
            target_w, target_h, scale = cover_size(width, height, *size)
            if scale >= MIN_SHRINK:
                return None
            icc = im.info.get("icc_profile")
            if im.format == "JPEG":
                im.draft("RGB", (target_w, target_h))  # decode at reduced size: fast and frugal
            im = ImageOps.exif_transpose(im)
            has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
            im = im.convert("RGBA" if has_alpha else "RGB")
            im = im.resize((target_w, target_h), Image.LANCZOS)
            out = path.with_suffix(".opt")
            params = {"icc_profile": icc} if icc else {}
            if has_alpha:
                im.save(out, "WEBP", quality=92, method=4, **params)
                out_mime = "image/webp"
            else:
                im.save(out, "JPEG", quality=94, subsampling=0, optimize=True, **params)
                out_mime = "image/jpeg"
        log.info("resized %dx%d original to %dx%d for this screen", width, height, target_w, target_h)
        return out, out_mime, [size[0], size[1]]

    return optimise
