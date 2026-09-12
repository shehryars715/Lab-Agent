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

    assert light.getpixel((5, 5)) != dark.getpixel((5, 5))
