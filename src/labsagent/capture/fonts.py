"""Monospace font resolution.

Order matters and is deliberate:

1. A font bundled in capture/assets/ -- the reproducible path. Output is then
   byte-identical on your machine, in CI, and on a cloud runner.
2. A system monospace font (Consolas on Windows). Authentic, available today,
   but output drifts between machines.
3. Pillow's built-in bitmap font. Ugly; a loud last resort.

To take option 1, drop a redistributable monospace TTF (DejaVu Sans Mono is the
usual choice) into capture/assets/. We do not ship Consolas: it is Microsoft's
and not ours to redistribute.
"""

from __future__ import annotations

from pathlib import Path

from PIL import ImageFont

ASSETS = Path(__file__).parent / "assets"

_SYSTEM_CANDIDATES = (
    Path("C:/Windows/Fonts/consola.ttf"),
    Path("C:/Windows/Fonts/cour.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    Path("/System/Library/Fonts/Menlo.ttc"),
)


def _bundled() -> Path | None:
    if not ASSETS.is_dir():
        return None
    for suffix in ("*.ttf", "*.otf"):
        for candidate in sorted(ASSETS.glob(suffix)):
            return candidate
    return None


def resolve(size: int) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, str]:
    """Return (font, provenance). Provenance is surfaced so a drifting render is
    traceable to its font rather than being a silent mystery."""
    bundled = _bundled()
    if bundled:
        return ImageFont.truetype(str(bundled), size), f"bundled:{bundled.name}"

    for candidate in _SYSTEM_CANDIDATES:
        if candidate.exists():
            try:
                return ImageFont.truetype(str(candidate), size), f"system:{candidate.name}"
            except OSError:
                continue

    return ImageFont.load_default(), "pillow-default"
