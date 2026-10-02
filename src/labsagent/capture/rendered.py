"""Transcript -> PNG that looks like the real window the program ran in.

THE OUTPUT IS REAL; THE WINDOW IS A FAITHFUL COPY. Every character in the
picture is what the program actually printed, captured by the shim. What used
to give the picture away was everything around it: black-on-white "PowerShell"
(no real PowerShell is white), a flat grey title bar set in a code font,
lines packed so tight that descenders touched, a made-up `PS C:\\lab>` prompt,
word-wrapping with an indent no terminal does, and no cursor. Capturing a real
window was rejected on 2026-09-26 (the server has no screen), so the window is
drawn instead -- as a copy of a specific real one, measured, not imagined:

- `terminal`: Windows Terminal running Windows PowerShell, out of the box --
  the Campbell scheme, Cascadia Mono (its default font, bundled), 8 px padding,
  the tab bar with the PowerShell icon, PSReadLine's yellow command word, hard
  wrapping at the column, tab stops every 8, colour codes interpreted, and the
  default bar cursor after the last prompt.
- `notebook`: a JupyterLab code cell for labs done in Jupyter or Colab -- the
  blue `[n]:` prompt, the grey editor box with its syntax colours, the output
  beneath, the selected cell's blue bar.

The owner chose the terminal's real dark default (2026-10-02). `light` is
Windows Terminal's own built-in "One Half Light" scheme, not invented colours.
"""

from __future__ import annotations

import builtins
import io
import keyword
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from labsagent.capture import fonts
from labsagent.models import Transcript

LOOKS = ("terminal", "notebook")


@dataclass(frozen=True)
class Scheme:
    """A Windows Terminal colour scheme, plus the window around it."""

    bg: str
    fg: str
    command: str
    cursor: str
    tabbar: str
    tab_fg: str
    border: str
    #: The 16 ANSI colours, normal then bright, in the scheme's own values.
    palette: tuple[str, ...]


CAMPBELL = Scheme(
    bg="#0C0C0C", fg="#CCCCCC", command="#F9F1A5", cursor="#FFFFFF",
    tabbar="#202020", tab_fg="#FFFFFF", border="#3A3A3A",
    palette=(
        "#0C0C0C", "#C50F1F", "#13A10E", "#C19C00", "#0037DA", "#881798", "#3A96DD", "#CCCCCC",
        "#767676", "#E74856", "#16C60C", "#F9F1A5", "#3B78FF", "#B4009E", "#61D6D6", "#F2F2F2",
    ),
)

ONE_HALF_LIGHT = Scheme(
    bg="#FAFAFA", fg="#383A42", command="#C18301", cursor="#4F525D",
    tabbar="#F3F3F3", tab_fg="#1A1A1A", border="#D0D0D0",
    palette=(
        "#383A42", "#E45649", "#50A14F", "#C18301", "#0184BC", "#A626A4", "#0997B3", "#FAFAFA",
        "#4F525D", "#DF6C75", "#98C379", "#E4C07A", "#61AFEF", "#C577DD", "#56B5C1", "#FFFFFF",
    ),
)

SCHEMES = {"dark": CAMPBELL, "light": ONE_HALF_LIGHT}
THEMES = SCHEMES  # the name older callers import

#: A generic user and the lab's own folder: real-looking, nobody's name in it.
PROMPT_TEMPLATE = "PS C:\\Users\\student\\Desktop\\{folder}>"

_SGR = re.compile(r"\x1b\[([0-9;]*)([A-Za-z])")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# JupyterLab's light theme, as CodeMirror colours Python in it.
NB_TEXT = "#212121"
NB_PROMPT = "#307FC1"
NB_EDITOR = "#F5F5F5"
NB_EDITOR_LINE = "#E0E0E0"
NB_SELECTED = "#1976D2"
NB_KEYWORD = "#008000"
NB_DEF = "#0000FF"
NB_STRING = "#BA2121"
NB_NUMBER = "#008800"
NB_COMMENT = "#3D7B7B"
NB_OPERATOR = "#AA22FF"
_BUILTINS = frozenset(dir(builtins))
_PUNCTUATION = frozenset("()[]{},.:;")


def _segments(line: str, scheme: Scheme) -> list[tuple[str, str]]:
    """One line of output as (text, colour) runs, the way a terminal shows it.

    Colour codes are interpreted, not printed: a program that prints in green
    shows green, as it did in the real terminal, instead of "[32m" and tofu.
    """
    out: list[tuple[str, str]] = []
    colour, position = scheme.fg, 0
    for match in _SGR.finditer(line):
        if match.start() > position:
            out.append((line[position:match.start()], colour))
        if match.group(2) == "m":
            for code in (match.group(1) or "0").split(";"):
                n = int(code) if code.isdigit() else 0
                if n in (0, 39):
                    colour = scheme.fg
                elif 30 <= n <= 37:
                    colour = scheme.palette[n - 30]
                elif 90 <= n <= 97:
                    colour = scheme.palette[n - 90 + 8]
        position = match.end()
    if position < len(line):
        out.append((line[position:], colour))
    return [(_CONTROL.sub("", text), c) for text, c in out if _CONTROL.sub("", text)]


def _wrap(runs: list[tuple[str, str]], cols: int) -> list[list[tuple[str, str]]]:
    """Hard-wrap at the column, mid-word, with no indent: what a terminal does."""
    rows: list[list[tuple[str, str]]] = []
    row: list[tuple[str, str]] = []
    used = 0
    for text, colour in runs:
        while text:
            if used == cols:
                rows.append(row)
                row, used = [], 0
            piece, text = text[: cols - used], text[cols - used:]
            row.append((piece, colour))
            used += len(piece)
    rows.append(row)
    return rows


def _highlight(code: str) -> list[list[tuple[str, str]]]:
    """Each line of code as (text, colour) runs, in JupyterLab's colours."""
    lines = code.split("\n")
    paint: list[list[str]] = [[NB_TEXT] * len(line) for line in lines]

    def colour_span(start, end, colour) -> None:
        (sr, sc), (er, ec) = start, end
        for row in range(sr - 1, er):
            if row >= len(paint):
                break
            first = sc if row == sr - 1 else 0
            last = ec if row == er - 1 else len(paint[row])
            for col in range(first, min(last, len(paint[row]))):
                paint[row][col] = colour

    fstring_start = getattr(tokenize, "FSTRING_START", None)
    fstring_end = getattr(tokenize, "FSTRING_END", None)
    try:
        after_def, after_dot, fstring_from, depth = False, False, None, 0
        for tok in tokenize.generate_tokens(io.StringIO(code).readline):
            kind, text = tok.type, tok.string
            if fstring_start is not None and kind == fstring_start:
                depth += 1
                fstring_from = fstring_from or tok.start
                continue
            if fstring_end is not None and kind == fstring_end:
                depth -= 1
                if depth == 0:
                    colour_span(fstring_from, tok.end, NB_STRING)
                    fstring_from = None
                continue
            if depth:
                continue
            colour = None
            if kind == tokenize.NAME:
                if keyword.iskeyword(text):
                    colour = NB_KEYWORD
                elif after_def:
                    colour = NB_DEF
                # `df.sum()` is a method, not the builtin `sum`: Jupyter colours
                # a builtin only where it is one.
                elif text in _BUILTINS and not after_dot:
                    colour = NB_KEYWORD
                after_def = text in ("def", "class")
            else:
                after_def = False
                colour = {
                    tokenize.STRING: NB_STRING,
                    tokenize.NUMBER: NB_NUMBER,
                    tokenize.COMMENT: NB_COMMENT,
                }.get(kind)
                # Operators are purple in Jupyter; brackets and punctuation
                # are not.
                if kind == tokenize.OP and text not in _PUNCTUATION:
                    colour = NB_OPERATOR
            after_dot = kind == tokenize.OP and text == "."
            if colour:
                colour_span(tok.start, tok.end, colour)
    except (tokenize.TokenError, SyntaxError):
        pass  # an incomplete fragment still shows, uncoloured past the error

    runs: list[list[tuple[str, str]]] = []
    for line, colours in zip(lines, paint):
        row: list[tuple[str, str]] = []
        for char, colour in zip(line, colours):
            if row and row[-1][1] == colour:
                row[-1] = (row[-1][0] + char, colour)
            else:
                row.append((char, colour))
        runs.append(row)
    return runs


class RenderedBackend:
    """Draws a transcript as a faithful copy of a real terminal or notebook."""

    def __init__(
        self,
        theme: str = "dark",
        font_size: int = 16,
        max_cols: int = 100,
        max_lines: int = 40,
        padding: int = 8,
        title: str = "Windows PowerShell",
        scale: int = 2,
        look: str = "terminal",
        folder: str = "",
    ) -> None:
        self.scheme = SCHEMES.get(theme, CAMPBELL)
        self.theme = self.scheme
        self.look = look if look in LOOKS else "terminal"
        self.scale = scale
        self.max_cols = max_cols
        self.max_lines = max_lines
        self.padding = padding * scale
        self.title = title
        self.prompt = PROMPT_TEMPLATE.format(folder=folder) if folder else ""
        self.font_size = font_size * scale
        if self.look == "notebook":
            self.font_size = 13 * scale
            self.font, self.font_provenance = fonts.mono(self.font_size)
        else:
            self.font, self.font_provenance = fonts.terminal(self.font_size)
        self.ui_font, _ = fonts.ui(12 * scale)
        self.char_w = max(1.0, self.font.getlength("M") if hasattr(self.font, "getlength") else 8)
        # Windows Terminal's cell is the font's own height: Cascadia Mono's
        # ascender + descender is 2380/2048 em. JupyterLab sets 1.31. Neither
        # is the old renderer's guess, which let descenders touch.
        self.line_h = round(self.font_size * (1.31 if self.look == "notebook" else 1.17))

    # ------------------------------------------------------------- shared

    def _u(self, logical: float) -> int:
        return round(logical * self.scale)

    def _text_runs(self, draw, x0: float, y: int, runs) -> None:
        col = 0
        for text, colour in runs:
            draw.text((x0 + col * self.char_w, y), text, font=self.font, fill=colour)
            col += len(text)

    # ----------------------------------------------------------- terminal

    def _rows(self, transcript: Transcript) -> list[tuple[str, list]]:
        prompt = self.prompt or transcript.prompt
        rows: list[tuple[str, list]] = []
        if transcript.head:
            command = transcript.command.split(" ", 1)
            runs = [(f"{prompt} ", self.scheme.fg), (command[0], self.scheme.command)]
            if len(command) > 1:
                runs.append((" " + command[1], self.scheme.fg))
            rows.append(("command", runs))
        for line in transcript.lines:
            runs = _segments(line.expandtabs(8), self.scheme)
            rows += [("out", row) for row in _wrap(runs, self.max_cols)]
        if transcript.tail:
            rows.append(("cursor", [(f"{prompt} ", self.scheme.fg)]))
        return rows

    def _draw_controls(self, draw, width: int, band_h: int) -> None:
        """Minimise, maximise, close: drawn as strokes, like the real ones."""
        c, cy = self.scheme.tab_fg, band_h // 2
        half, stroke = self._u(5), max(1, self.scale)
        for slot, kind in enumerate(("close", "max", "min")):
            cx = width - self._u(23) - slot * self._u(46)
            if kind == "close":
                draw.line([cx - half, cy - half, cx + half, cy + half], fill=c, width=stroke)
                draw.line([cx - half, cy + half, cx + half, cy - half], fill=c, width=stroke)
            elif kind == "max":
                draw.rectangle([cx - half, cy - half, cx + half, cy + half], outline=c, width=stroke)
            else:
                draw.line([cx - half, cy, cx + half, cy], fill=c, width=stroke)

    def _draw_tab(self, draw, tab_h: int) -> None:
        u, s = self._u, self.scheme
        x0, top = u(8), u(8)
        title_w = draw.textlength(self.title, font=self.ui_font)
        x1 = x0 + u(12) + u(16) + u(10) + int(title_w) + u(36)
        draw.rounded_rectangle([x0, top, x1, tab_h + u(8)], radius=u(8), fill=s.bg)
        cy = top + (tab_h - top) // 2
        # The PowerShell icon: a blue tile with ">_".
        ix = x0 + u(12)
        draw.rounded_rectangle([ix, cy - u(8), ix + u(16), cy + u(8)], radius=u(3), fill="#2B65C4")
        white, stroke = "#FFFFFF", max(1, self.scale)
        draw.line([ix + u(4), cy - u(4), ix + u(8), cy, ix + u(4), cy + u(4)], fill=white, width=stroke)
        draw.line([ix + u(9), cy + u(4), ix + u(13), cy + u(4)], fill=white, width=stroke)
        draw.text((ix + u(26), cy), self.title, font=self.ui_font, fill=s.tab_fg, anchor="lm")
        # The tab's own close button, then "+" and the profile dropdown.
        grey, half = "#A0A0A0" if s is CAMPBELL else "#5A5A5A", u(4)
        cx = x1 - u(18)
        draw.line([cx - half, cy - half, cx + half, cy + half], fill=grey, width=stroke)
        draw.line([cx - half, cy + half, cx + half, cy - half], fill=grey, width=stroke)
        px = x1 + u(22)
        draw.line([px - u(6), cy, px + u(6), cy], fill=grey, width=stroke)
        draw.line([px, cy - u(6), px, cy + u(6)], fill=grey, width=stroke)
        dx = px + u(30)
        draw.line([dx - u(5), cy - u(2), dx, cy + u(3), dx + u(5), cy - u(2)], fill=grey, width=stroke)

    def _draw_terminal(self, rows, out_path: Path) -> Path:
        u, s = self._u, self.scheme
        tab_h, pad = u(40), self.padding
        width = int(pad * 2 + self.char_w * self.max_cols)
        height = tab_h + pad * 2 + self.line_h * max(1, len(rows))

        # Transparent outside the rounded corners, so the page shows through
        # the way it does around a real Windows 11 window.
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        radius = u(8)
        draw.rounded_rectangle([0, 0, width - 1, height - 1], radius=radius, fill=s.bg)
        draw.rounded_rectangle([0, 0, width - 1, tab_h + radius], radius=radius, fill=s.tabbar)
        draw.rectangle([0, tab_h, width - 1, tab_h + radius], fill=s.bg)
        self._draw_tab(draw, tab_h)
        self._draw_controls(draw, width, u(40))

        y = tab_h + pad
        for kind, runs in rows:
            self._text_runs(draw, pad, y, runs)
            if kind == "cursor":
                x = pad + self.char_w * sum(len(t) for t, _ in runs)
                draw.rectangle([x, y + u(1), x + max(1, self.scale) - 1, y + self.line_h - u(2)],
                               fill=s.cursor)
            y += self.line_h

        draw.rounded_rectangle([0, 0, width - 1, height - 1], radius=radius,
                               outline=s.border, width=max(1, self.scale // 2))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, "PNG")
        return out_path

    # ----------------------------------------------------------- notebook

    def _draw_notebook(self, code: str, rows, cell: int, out_path: Path, with_code: bool) -> Path:
        u = self._u
        prompt_w, inset = u(64), u(8)
        code_rows = _highlight(code.rstrip("\n")) if with_code and code.strip() else []
        width = int(prompt_w + inset * 2 + self.char_w * self.max_cols + u(12))
        editor_h = (self.line_h * len(code_rows) + u(10)) if code_rows else 0
        height = u(8) + editor_h + (u(8) if code_rows else 0) + self.line_h * len(rows) + u(10)

        img = Image.new("RGB", (width, max(height, u(24))), "#FFFFFF")
        draw = ImageDraw.Draw(img)
        # The selected cell's bar: the cell you just ran is the selected one.
        draw.rectangle([u(2), u(6), u(5), height - u(6)], fill=NB_SELECTED)

        y = u(8)
        if code_rows:
            draw.text((prompt_w - u(6), y + u(5)), f"[{cell}]:", font=self.font,
                      fill=NB_PROMPT, anchor="ra")
            draw.rectangle([prompt_w, y, width - u(12), y + editor_h],
                           fill=NB_EDITOR, outline=NB_EDITOR_LINE, width=max(1, self.scale // 2))
            line_y = y + u(5)
            for runs in code_rows:
                self._text_runs(draw, prompt_w + inset, line_y, runs)
                line_y += self.line_h
            y += editor_h + u(8)
        for _, runs in rows:
            self._text_runs(draw, prompt_w + inset, y, runs)
            y += self.line_h

        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, "PNG")
        return out_path

    def _output_rows(self, transcript: Transcript) -> list[tuple[str, list]]:
        """A notebook prints no prompt and no command: just what came out, in
        Jupyter's text colour, still hard-wrapped."""
        rows: list[tuple[str, list]] = []
        for line in transcript.lines:
            runs = [(text, NB_TEXT) for text, _ in _segments(line.expandtabs(8), self.scheme)]
            rows += [("out", row) for row in _wrap(runs, self.max_cols)]
        return rows

    # ------------------------------------------------------------- public

    def render(self, transcript: Transcript, out_path: Path) -> list[Path]:
        out_path = Path(out_path)
        if self.look == "notebook":
            rows = self._output_rows(transcript)
        else:
            rows = self._rows(transcript)
        chunks = [rows[i: i + self.max_lines] for i in range(0, len(rows), self.max_lines)] or [[]]

        def draw(chunk, path, first):
            if self.look == "notebook":
                code = getattr(transcript, "code", "") or ""
                cell = getattr(transcript, "cell", 1) or 1
                return self._draw_notebook(code, chunk, cell, path, with_code=first)
            return self._draw_terminal(chunk, path)

        if len(chunks) == 1:
            return [draw(chunks[0], out_path, True)]
        # Paginate rather than emit one unreadably tall image: one window per
        # page, the way a student scrolls and takes another screenshot.
        return [
            draw(chunk, out_path.with_name(f"{out_path.stem}_p{n}{out_path.suffix}"), n == 1)
            for n, chunk in enumerate(chunks, start=1)
        ]
