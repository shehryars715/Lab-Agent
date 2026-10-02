from __future__ import annotations

from PIL import Image

from labsagent.capture.base import ScreenshotBackend
from labsagent.capture.rendered import RenderedBackend
from labsagent.models import Transcript


def test_satisfies_protocol():
    assert isinstance(RenderedBackend(), ScreenshotBackend)


def test_single_page_for_short_output(tmp_path):
    t = Transcript(command="python t.py", lines=["Sum = 8"])
    paths = RenderedBackend().render(t, tmp_path / "o.png")

    assert len(paths) == 1
    assert Image.open(paths[0]).size[0] > 0


def test_long_output_paginates(tmp_path):
    t = Transcript(command="python t.py", lines=[f"line {i}" for i in range(120)])
    paths = RenderedBackend(max_lines=40).render(t, tmp_path / "o.png")

    assert len(paths) > 1
    assert all(p.exists() for p in paths)
    assert paths[0].name == "o_p1.png"


def test_long_lines_wrap_rather_than_clip(tmp_path):
    t = Transcript(command="python t.py", lines=["x" * 400])
    short = RenderedBackend(max_cols=60).render(t, tmp_path / "a.png")[0]
    wide = RenderedBackend(max_cols=200).render(t, tmp_path / "b.png")[0]

    assert Image.open(short).size[1] > Image.open(wide).size[1]


def test_themes_differ(tmp_path):
    t = Transcript(command="python t.py", lines=["Sum = 8"])
    light = Image.open(RenderedBackend(theme="light").render(t, tmp_path / "l.png")[0])
    dark = Image.open(RenderedBackend(theme="dark").render(t, tmp_path / "d.png")[0])

    # Inside the terminal body: the corners are transparent in both.
    probe = (light.size[0] - 30, light.size[1] - 10)
    assert light.getpixel(probe) != dark.getpixel(probe)


# --- a faithful copy of the real window (2026-10-03) ---------------------------


def test_the_default_is_windows_terminal_dark_with_its_own_font(tmp_path):
    backend = RenderedBackend()
    image = Image.open(backend.render(Transcript("python t.py", ["8"]), tmp_path / "t.png")[0])
    assert backend.font_provenance == "bundled:CascadiaMono-Regular.ttf"
    assert image.mode == "RGBA" and image.getpixel((0, 0))[3] == 0, "rounded, see-through corners"
    assert image.getpixel((image.size[0] // 2, image.size[1] - 12))[:3] == (12, 12, 12)  # Campbell


def test_the_prompt_is_a_real_looking_folder_with_no_name_in_it():
    rows = RenderedBackend(folder="Lab 03")._rows(Transcript("python task1.py", ["8"]))
    command = "".join(text for text, _ in rows[0][1])
    assert command == r"PS C:\Users\student\Desktop\Lab 03> python task1.py"
    assert rows[-1][0] == "cursor"


def test_output_hard_wraps_like_a_terminal_with_no_indent():
    rows = RenderedBackend(max_cols=10)._rows(Transcript("python t.py", ["abcdefghijKLMNO"], head=False, tail=False))
    assert ["".join(t for t, _ in runs) for _, runs in rows] == ["abcdefghij", "KLMNO"]


def test_colour_codes_are_shown_as_colour_not_printed():
    from labsagent.capture.rendered import CAMPBELL, _segments

    assert _segments("\x1b[32mok\x1b[0m done", CAMPBELL) == [("ok", "#13A10E"), (" done", "#CCCCCC")]


def test_a_notebook_lab_gets_a_jupyter_cell_with_its_code(tmp_path):
    from labsagent.capture.rendered import NB_DEF, NB_KEYWORD, NB_TEXT, _highlight

    t = Transcript("python t.py", ["8"], code="def add(a, b):\n    return a + b\nprint(df.sum())", cell=3)
    (path,) = RenderedBackend(look="notebook").render(t, tmp_path / "n.png")
    assert Image.open(path).getpixel((10, 10)) == (255, 255, 255)
    first, _, last = _highlight(t.code)
    assert first[0] == ("def", NB_KEYWORD) and ("add", NB_DEF) in first
    assert all(colour == NB_TEXT for text, colour in last if "sum" in text), (
        "a method is not the builtin sum"
    )


def test_the_pipeline_picks_the_look_from_the_lab():
    from types import SimpleNamespace

    from web.server.pipeline import screenshot_backend

    spec = SimpleNamespace(lab_number="04")
    colab = SimpleNamespace(paragraphs=[SimpleNamespace(text="Open Google Colab and run each cell.")])
    plain = SimpleNamespace(paragraphs=[SimpleNamespace(text="Write a program that adds.")])
    assert screenshot_backend(spec, colab, "lab", None).look == "notebook"
    assert screenshot_backend(spec, plain, "notebook_lab", None).look == "notebook"
    terminal = screenshot_backend(spec, plain, "lab", None)
    assert terminal.look == "terminal" and terminal.prompt.endswith(r"Desktop\Lab 04>")
