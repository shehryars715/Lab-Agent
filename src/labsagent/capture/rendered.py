"""Transcript -> PNG, styled as a Windows terminal window.

Light theme by default: a printed lab report is on white paper, and a dark
screenshot becomes a heavy ink block that photocopies badly.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from labsagent.capture import fonts
from labsagent.models import Transcript


@dataclass(frozen=True)
class Theme:
    bg: str
    fg: str
    chrome_bg: str
    chrome_fg: str
    border: str
    prompt_fg: str
    stderr_fg: str


LIGHT = Theme(
    bg="#FFFFFF",
    fg="#0C0C0C",
    chrome_bg="#E9E9E9",
    chrome_fg="#3C3C3C",
    border="#C8C8C8",
    prompt_fg="#0058A3",
    stderr_fg="#A31515",
)

DARK = Theme(
    bg="#0C0C0C",
    fg="#CCCCCC",
    chrome_bg="#2D2D2D",
    chrome_fg="#CCCCCC",
    border="#3C3C3C",
    prompt_fg="#3A96DD",
    stderr_fg="#E74856",
)

THEMES = {"light": LIGHT, "dark": DARK}


class RenderedBackend:
    """Draws a transcript as a terminal-styled PNG."""

    def __init__(
        self,
        theme: str = "light",
        font_size: int = 15,
        max_cols: int = 96,
        max_lines: int = 40,
        padding: int = 14,
        title: str = "Windows PowerShell",
        scale: int = 2,
    ) -> None:
        self.theme = THEMES[theme]
        self.font_size = font_size * scale
        self.max_cols = max_cols
        self.max_lines = max_lines
        self.padding = padding * scale
        self.title = title
        self.scale = scale
        self.font, self.font_provenance = fonts.resolve(self.font_size)

    def _wrap(self, lines: list[str]) -> list[str]:
        out: list[str] = []
        for line in lines:
            if not line:
                out.append("")
            elif len(line) <= self.max_cols:
                out.append(line)
            else:
                out.extend(
                    textwrap.wrap(
                        line,
                        width=self.max_cols,
                        subsequent_indent="  ",
                        drop_whitespace=False,
                        break_long_words=True,
                    )
                    or [""]
                )
        return out

    def _metrics(self) -> tuple[int, int]:
        probe = Image.new("RGB", (1, 1))
        draw = ImageDraw.Draw(probe)
        box = draw.textbbox((0, 0), "M" * 10, font=self.font)
        char_w = max(1, (box[2] - box[0]) // 10)
        line_h = int((box[3] - box[1]) * 1.45) or self.font_size
        return char_w, line_h

    def _draw_controls(self, draw: ImageDraw.ImageDraw, width: int, chrome_h: int) -> None:
        """Minimise / maximise / close, drawn as shapes so no font can fail us."""
        c = self.theme.chrome_fg
        w = max(1, self.scale)
        size = int(self.font_size * 0.45)
        gap = int(self.font_size * 1.9)
        cy = chrome_h // 2
        cx = width - self.padding - size // 2

        # close
        draw.line([cx - size // 2, cy - size // 2, cx + size // 2, cy + size // 2], fill=c, width=w)
        draw.line([cx - size // 2, cy + size // 2, cx + size // 2, cy - size // 2], fill=c, width=w)
        # maximise
        cx -= gap
        draw.rectangle(
            [cx - size // 2, cy - size // 2, cx + size // 2, cy + size // 2], outline=c, width=w
        )
        # minimise
        cx -= gap
        draw.line([cx - size // 2, cy, cx + size // 2, cy], fill=c, width=w)

    def _draw_page(self, lines: list[str], prompt: str, out_path: Path) -> Path:
        char_w, line_h = self._metrics()
        chrome_h = int(self.font_size * 1.9)

        width = self.padding * 2 + char_w * self.max_cols
        height = chrome_h + self.padding * 2 + line_h * max(1, len(lines))

        img = Image.new("RGB", (width, height), self.theme.bg)
        draw = ImageDraw.Draw(img)

        # Title bar with window controls.
        draw.rectangle([0, 0, width, chrome_h], fill=self.theme.chrome_bg)
        draw.text(
            (self.padding, chrome_h // 2),
            self.title,
            font=self.font,
            fill=self.theme.chrome_fg,
            anchor="lm",
        )
        # Draw window controls as vectors, not glyphs: Consolas has no box or
        # cross glyph and silently renders tofu.
        self._draw_controls(draw, width, chrome_h)
        draw.line([0, chrome_h, width, chrome_h], fill=self.theme.border, width=1)

        y = chrome_h + self.padding
        for line in lines:
            if line.startswith(prompt):
                # Colour the prompt, leave the typed command in the body colour.
                draw.text((self.padding, y), prompt, font=self.font, fill=self.theme.prompt_fg)
                draw.text(
                    (self.padding + char_w * len(prompt), y),
                    line[len(prompt):],
                    font=self.font,
                    fill=self.theme.fg,
                )
            else:
                draw.text((self.padding, y), line, font=self.font, fill=self.theme.fg)
            y += line_h

        draw.rectangle([0, 0, width - 1, height - 1], outline=self.theme.border, width=1)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, "PNG")
        return out_path

    def render(self, transcript: Transcript, out_path: Path) -> list[Path]:
        lines = self._wrap(transcript.display_lines())
        out_path = Path(out_path)

        if len(lines) <= self.max_lines:
            return [self._draw_page(lines, transcript.prompt, out_path)]

        # Paginate rather than emit one unreadably tall image.
        pages: list[Path] = []
        chunks = [lines[i : i + self.max_lines] for i in range(0, len(lines), self.max_lines)]
        for n, chunk in enumerate(chunks, start=1):
            page_path = out_path.with_name(f"{out_path.stem}_p{n}{out_path.suffix}")
            pages.append(self._draw_page(chunk, transcript.prompt, page_path))
        return pages
