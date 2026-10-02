"""Font resolution for the screenshot looks.

A screenshot reads as real when its fonts are the ones the real window uses,
so each look asks for its own:

- the TERMINAL look uses Cascadia Mono, Windows Terminal's default font. It
  ships in capture/assets/ (SIL Open Font License, see CASCADIA-OFL.txt), so
  the picture is identical on this machine and on the Linux server, which has
  no Cascadia of its own;
- the NOTEBOOK look uses the monospace font Jupyter itself would pick on that
  platform (Consolas on Windows, DejaVu Sans Mono on Linux);
- window titles use the platform's interface font (Segoe UI on Windows, DejaVu
  Sans on Linux). Segoe UI is Microsoft's and not ours to redistribute.

Pillow's built-in bitmap font is the loud last resort for every role.
"""

from __future__ import annotations

from pathlib import Path

from PIL import ImageFont

ASSETS = Path(__file__).parent / "assets"
TERMINAL_FONT = ASSETS / "CascadiaMono-Regular.ttf"

_MONO = (
    Path("C:/Windows/Fonts/consola.ttf"),
    Path("C:/Windows/Fonts/cour.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    Path("/System/Library/Fonts/Menlo.ttc"),
)
_MONO_BOLD = (
    Path("C:/Windows/Fonts/consolab.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"),
)
_UI = (
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/System/Library/Fonts/SFNS.ttf"),
)


def _first(candidates, size: int) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, str]:
    for candidate in candidates:
        if candidate.exists():
            try:
                return ImageFont.truetype(str(candidate), size), candidate.name
            except OSError:
                continue
    return ImageFont.load_default(), "pillow-default"


def terminal(size: int):
    """(font, provenance) for the terminal look: the bundled Cascadia Mono."""
    font, name = _first((TERMINAL_FONT, *_MONO), size)
    return font, (f"bundled:{name}" if name == TERMINAL_FONT.name else f"system:{name}")


def mono(size: int, bold: bool = False):
    """(font, provenance) for the notebook look: the platform's own monospace."""
    font, name = _first((*_MONO_BOLD, *_MONO) if bold else _MONO, size)
    return font, f"system:{name}"


def ui(size: int):
    """(font, provenance) for window titles."""
    font, name = _first(_UI, size)
    return font, f"system:{name}"


def resolve(size: int):
    """The terminal font. Kept for callers that predate the looks."""
    return terminal(size)
